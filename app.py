import json
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict, List

import requests
from flask import Flask, jsonify, request, send_from_directory


KIMI_ENDPOINT = os.getenv("KIMI_API_ENDPOINT", "https://api.moonshot.cn/v1/chat/completions")
OPENAI_ENDPOINT = os.getenv("OPENAI_API_ENDPOINT", "https://api.openai.com/v1/chat/completions")
DRIVE_UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
DRIVE_LIST_ENDPOINT = "https://www.googleapis.com/drive/v3/files"

ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "20"))
MAX_CELL_CHARS = int(os.getenv("MAX_CELL_CHARS", "2000"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "500"))


class InMemoryRateLimiter:
    def __init__(self, window_seconds: int, max_requests: int):
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._requests: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        bucket = self._requests[key]
        while bucket and (now - bucket[0]) > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(now)
        return True


rate_limiter = InMemoryRateLimiter(
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    max_requests=RATE_LIMIT_MAX_REQUESTS,
)

app = Flask(__name__, static_folder="static", static_url_path="/static")


def _log(level: str, message: str, **fields):
    payload = {"level": level, "message": message, **fields}
    print(json.dumps(payload), flush=True)


def _request_id() -> str:
    return request.headers.get("X-Request-Id", str(uuid.uuid4()))


def _cors_origin() -> str:
    origin = request.headers.get("Origin", "")
    if "*" in ALLOWED_ORIGINS:
        return "*"
    if origin and origin in ALLOWED_ORIGINS:
        return origin
    return ""


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _require_json():
    if not request.is_json:
        return jsonify({"error": "JSON required"}), 400
    return None


def _sanitize_sheet_data(payload: dict) -> dict:
    """Reduce prompt injection and oversized data risk."""
    rows = payload.get("sheetData", [])
    if not isinstance(rows, list):
        return payload

    sanitized = []
    for row in rows[:MAX_ROWS]:
        if not isinstance(row, list):
            continue
        sanitized_row = []
        for cell in row:
            cell_text = str(cell)
            cell_text = cell_text.replace("<script", "<blocked-script")
            cell_text = cell_text.replace("```", "` ` `")
            sanitized_row.append(cell_text[:MAX_CELL_CHARS])
        sanitized.append(sanitized_row)

    updated = dict(payload)
    updated["sheetData"] = sanitized
    return updated


def _proxy_ai(endpoint: str, api_key: str, payload: dict):
    upstream = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    return jsonify(upstream.json()), upstream.status_code


def _user_oauth_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


@app.after_request
def add_security_headers(resp):
    request_id = getattr(request, "request_id", None)
    if request_id:
        resp.headers["X-Request-Id"] = request_id

    cors_origin = _cors_origin()
    if cors_origin:
        resp.headers["Access-Control-Allow-Origin"] = cors_origin
        resp.headers["Vary"] = "Origin"

    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers[
        "Content-Security-Policy"
    ] = (
        "default-src 'self'; "
        "script-src 'self' https://accounts.google.com https://apis.google.com; "
        "connect-src 'self' https://apis.google.com https://www.googleapis.com https://api.moonshot.cn https://api.openai.com; "
        "img-src 'self' data: https://lh3.googleusercontent.com; "
        "style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none';"
    )
    return resp


@app.before_request
def before_request_logging_and_rate_limit():
    request.request_id = _request_id()
    key = f"{_client_ip()}:{request.path}"
    if request.path.startswith("/api/") and not rate_limiter.allow(key):
        _log("warn", "rate_limit_exceeded", request_id=request.request_id, path=request.path, ip=_client_ip())
        return jsonify({"error": "Rate limit exceeded", "requestId": request.request_id}), 429


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True, "requestId": request.request_id})


@app.route("/api/config", methods=["GET"])
def config():
    return jsonify(
        {
            "googleClientId": os.getenv("GOOGLE_CLIENT_ID", ""),
            "defaultProvider": os.getenv("DEFAULT_AI_PROVIDER", "kimi"),
            "allowedProviders": ["kimi", "openai"],
            "scopes": {
                "base": "https://www.googleapis.com/auth/spreadsheets",
                "drive": "https://www.googleapis.com/auth/drive.file",
            },
            "rateLimit": {
                "windowSeconds": RATE_LIMIT_WINDOW_SECONDS,
                "maxRequests": RATE_LIMIT_MAX_REQUESTS,
            },
        }
    )


@app.route("/api/ai/kimi", methods=["POST"])
def proxy_kimi():
    invalid = _require_json()
    if invalid:
        return invalid

    payload = request.get_json(silent=True) or {}
    if "messages" not in payload:
        return jsonify({"error": "`messages` field is required"}), 400

    api_key = os.getenv("KIMI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "KIMI_API_KEY not configured on server"}), 500

    try:
        return _proxy_ai(KIMI_ENDPOINT, api_key, payload)
    except requests.exceptions.Timeout:
        return jsonify({"error": "AI service timeout"}), 504
    except requests.exceptions.RequestException as err:
        return jsonify({"error": f"AI service network error: {err}"}), 502


@app.route("/api/ai/openai", methods=["POST"])
def proxy_openai():
    invalid = _require_json()
    if invalid:
        return invalid

    payload = request.get_json(silent=True) or {}
    if "messages" not in payload:
        return jsonify({"error": "`messages` field is required"}), 400

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY not configured on server"}), 500

    try:
        return _proxy_ai(OPENAI_ENDPOINT, api_key, payload)
    except requests.exceptions.Timeout:
        return jsonify({"error": "AI service timeout"}), 504
    except requests.exceptions.RequestException as err:
        return jsonify({"error": f"AI service network error: {err}"}), 502


@app.route("/api/ai/analyze", methods=["POST"])
def analyze_with_fallback():
    invalid = _require_json()
    if invalid:
        return invalid

    payload = _sanitize_sheet_data(request.get_json(silent=True) or {})
    providers: List[str] = payload.get("providerOrder", ["kimi", "openai"])
    errors = []

    for provider in providers:
        if provider == "kimi":
            key = os.getenv("KIMI_API_KEY", "")
            endpoint = KIMI_ENDPOINT
        elif provider == "openai":
            key = os.getenv("OPENAI_API_KEY", "")
            endpoint = OPENAI_ENDPOINT
        else:
            errors.append({"provider": provider, "error": "Unsupported provider"})
            continue

        if not key:
            errors.append({"provider": provider, "error": "API key missing"})
            continue

        try:
            upstream = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload.get("aiPayload", {}),
                timeout=60,
            )
            if upstream.ok:
                result = upstream.json()
                result["provider"] = provider
                return jsonify(result), 200
            errors.append({"provider": provider, "error": upstream.text[:500], "status": upstream.status_code})
        except requests.exceptions.RequestException as err:
            errors.append({"provider": provider, "error": str(err)})

    return jsonify({"error": "All providers failed", "attempts": errors}), 502


@app.route("/api/drive/save", methods=["POST"])
def drive_save():
    invalid = _require_json()
    if invalid:
        return invalid

    token = _user_oauth_token()
    if not token:
        return jsonify({"error": "Missing Google OAuth bearer token"}), 401

    body = request.get_json(silent=True) or {}
    file_name = body.get("fileName", f"analysis-{int(time.time())}.json")
    content = body.get("content", {})
    parents = ["appDataFolder"] if body.get("useAppDataFolder", True) else []

    metadata = {
        "name": file_name,
        "mimeType": "application/json",
    }
    if parents:
        metadata["parents"] = parents

    files = {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "file": (file_name, json.dumps(content, ensure_ascii=False), "application/json"),
    }
    try:
        upstream = requests.post(
            DRIVE_UPLOAD_ENDPOINT,
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            timeout=60,
        )
        return jsonify(upstream.json()), upstream.status_code
    except requests.exceptions.RequestException as err:
        return jsonify({"error": f"Drive save failed: {err}"}), 502


@app.route("/api/drive/list", methods=["GET"])
def drive_list():
    token = _user_oauth_token()
    if not token:
        return jsonify({"error": "Missing Google OAuth bearer token"}), 401

    params = {
        "q": "'appDataFolder' in parents and trashed=false",
        "pageSize": min(int(request.args.get("pageSize", "25")), 100),
        "fields": "files(id,name,createdTime,modifiedTime,size)",
        "orderBy": "modifiedTime desc",
    }
    try:
        upstream = requests.get(
            DRIVE_LIST_ENDPOINT,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        return jsonify(upstream.json()), upstream.status_code
    except requests.exceptions.RequestException as err:
        return jsonify({"error": f"Drive list failed: {err}"}), 502


@app.route("/", defaults={"path": "index.html"})
@app.route("/<path:path>")
def static_proxy(path: str):
    if path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(app.static_folder, path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
