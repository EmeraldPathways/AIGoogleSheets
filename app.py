import json
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from functools import wraps
from typing import Any, Deque, Dict, List

import requests
from flask import Flask, jsonify, request, send_from_directory

try:
    import redis as redis_lib
except ImportError:
    redis_lib = None

try:
    from google.cloud import secretmanager
except ImportError:
    secretmanager = None


KIMI_ENDPOINT = os.getenv("KIMI_API_ENDPOINT", "https://api.moonshot.ai/v1/chat/completions")
OPENAI_ENDPOINT = os.getenv("OPENAI_API_ENDPOINT", "https://api.openai.com/v1/chat/completions")
DRIVE_UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
DRIVE_LIST_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
GOOGLE_TOKENINFO_ENDPOINT = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GOOGLE_DRIVE_APPDATA_SCOPE = "https://www.googleapis.com/auth/drive.appdata"
GOOGLE_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SESSION_SCHEMA_VERSION = 2
OBSERVABILITY_HISTORY_LIMIT = int(os.getenv("OBSERVABILITY_HISTORY_LIMIT", "100"))
UPSTREAM_MAX_ATTEMPTS = int(os.getenv("UPSTREAM_MAX_ATTEMPTS", "3"))
UPSTREAM_RETRY_BASE_DELAY_MS = int(os.getenv("UPSTREAM_RETRY_BASE_DELAY_MS", "250"))
UPSTREAM_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
EMBED_ALLOWED_PARENTS = [
    parent.strip()
    for parent in os.getenv(
        "EMBED_ALLOWED_PARENTS",
        "https://script.google.com https://script.googleusercontent.com",
    ).split(" ")
    if parent.strip()
]
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "20"))
MAX_CELL_CHARS = int(os.getenv("MAX_CELL_CHARS", "2000"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "500"))
APP_ENV = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip() or "development"
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_RATE_LIMIT_PREFIX = os.getenv("REDIS_RATE_LIMIT_PREFIX", "rate-limit").strip() or "rate-limit"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
_secret_cache: Dict[str, str] = {}
_secret_lock = threading.Lock()
_secret_client = None
_observability_lock = threading.Lock()
_observability = {
    "counters": defaultdict(int),
    "durationsMs": defaultdict(float),
    "recentEvents": deque(maxlen=OBSERVABILITY_HISTORY_LIMIT),
}


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

    @property
    def backend(self) -> str:
        return "memory"


class RedisRateLimiter:
    def __init__(self, client: Any, window_seconds: int, max_requests: int, prefix: str):
        self.client = client
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self.prefix = prefix

    def allow(self, key: str) -> bool:
        now = time.time()
        redis_key = f"{self.prefix}:{key}"
        request_marker = f"{now}:{uuid.uuid4()}"
        pipeline = self.client.pipeline()
        pipeline.zremrangebyscore(redis_key, 0, now - self.window_seconds)
        pipeline.zcard(redis_key)
        pipeline.zadd(redis_key, {request_marker: now})
        pipeline.expire(redis_key, self.window_seconds)
        _, current_count, _, _ = pipeline.execute()
        return int(current_count) < self.max_requests

    @property
    def backend(self) -> str:
        return "redis"


def _log(level: str, message: str, **fields):
    payload = {"level": level, "message": message, "timestamp": time.time(), **fields}
    print(json.dumps(payload), flush=True)


def _create_secret_client():
    global _secret_client
    if _secret_client is not None or secretmanager is None:
        return _secret_client
    with _secret_lock:
        if _secret_client is None and secretmanager is not None:
            _secret_client = secretmanager.SecretManagerServiceClient()
    return _secret_client


def _read_secret(secret_resource: str) -> str:
    if not secret_resource:
        return ""
    if secretmanager is None:
        raise RuntimeError("google-cloud-secret-manager is not installed")
    client = _create_secret_client()
    response = client.access_secret_version(request={"name": secret_resource})
    return response.payload.data.decode("utf-8")


def _setting(name: str, default: str = "", secret_fallback: bool = False) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    if not secret_fallback:
        return default

    secret_resource = os.getenv(f"{name}_SECRET_RESOURCE", "").strip()
    if not secret_resource:
        return default

    with _secret_lock:
        if secret_resource in _secret_cache:
            return _secret_cache[secret_resource]

    try:
        secret_value = _read_secret(secret_resource)
    except Exception as err:  # pragma: no cover
        _log("error", "secret_lookup_failed", secret=name, resource=secret_resource, error=str(err))
        return default
    with _secret_lock:
        _secret_cache[secret_resource] = secret_value
    return secret_value or default


def _create_rate_limiter():
    if REDIS_URL and redis_lib is not None:
        try:
            client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
            client.ping()
            _log("info", "rate_limiter_initialized", backend="redis", env=APP_ENV)
            return RedisRateLimiter(
                client=client,
                window_seconds=RATE_LIMIT_WINDOW_SECONDS,
                max_requests=RATE_LIMIT_MAX_REQUESTS,
                prefix=REDIS_RATE_LIMIT_PREFIX,
            )
        except Exception as err:  # pragma: no cover
            _log("warn", "rate_limiter_fallback", backend="memory", reason=str(err))
    return InMemoryRateLimiter(
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        max_requests=RATE_LIMIT_MAX_REQUESTS,
    )


rate_limiter = _create_rate_limiter()
app = Flask(__name__, static_folder="static", static_url_path="/static")


def _record_metric(name: str, value: float = 1, **fields):
    entry = {"name": name, "value": value, "timestamp": time.time(), **fields}
    with _observability_lock:
        _observability["counters"][name] += value
        if "durationMs" in fields:
            _observability["durationsMs"][name] += float(fields["durationMs"])
        _observability["recentEvents"].append(entry)
    _log("info", "metric", metric=name, value=value, **fields)


def _request_id() -> str:
    return request.headers.get("X-Request-Id", str(uuid.uuid4()))


def _trace_id() -> str:
    trace_header = request.headers.get("X-Cloud-Trace-Context", "")
    if trace_header:
        return trace_header.split("/", 1)[0].strip()
    return request.headers.get("X-Trace-Id", _request_id())


def _cors_origin() -> str:
    origin = request.headers.get("Origin", "")
    if "*" in ALLOWED_ORIGINS:
        return "*"
    if origin and origin in ALLOWED_ORIGINS:
        return origin
    return ""


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _json_response(payload: dict, status: int = 200):
    payload.setdefault("requestId", getattr(request, "request_id", ""))
    payload.setdefault("traceId", getattr(request, "trace_id", ""))
    return jsonify(payload), status


def _api_error(code: str, message: str, status: int, *, details: dict | None = None, retryable: bool = False):
    return _json_response(
        {
            "ok": False,
            "message": message,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "retryable": retryable,
            },
        },
        status,
    )


def _require_json():
    if not request.is_json:
        return _api_error("json_required", "JSON required", 400)
    return None


def _sanitize_sheet_data(payload: dict) -> dict:
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


def _parse_json_response(upstream: Any) -> dict:
    try:
        return upstream.json()
    except ValueError:
        return {"raw": upstream.text[:1000]}


def _request_with_retry(method: str, url: str, *, operation: str, retryable_statuses: set[int] | None = None, **kwargs):
    retryable_statuses = retryable_statuses or UPSTREAM_RETRYABLE_STATUSES
    last_response = None
    last_error = None

    for attempt in range(1, UPSTREAM_MAX_ATTEMPTS + 1):
        started = time.time()
        try:
            response = requests.request(method=method, url=url, **kwargs)
            last_response = response
            latency_ms = round((time.time() - started) * 1000, 2)
            _record_metric(
                "upstream_request",
                operation=operation,
                method=method,
                attempt=attempt,
                status=response.status_code,
                durationMs=latency_ms,
            )
            if response.status_code not in retryable_statuses or attempt == UPSTREAM_MAX_ATTEMPTS:
                return response
        except requests.exceptions.RequestException as err:
            last_error = err
            latency_ms = round((time.time() - started) * 1000, 2)
            _record_metric(
                "upstream_request_error",
                operation=operation,
                method=method,
                attempt=attempt,
                error=str(err),
                durationMs=latency_ms,
            )
            if attempt == UPSTREAM_MAX_ATTEMPTS:
                raise

        time.sleep((UPSTREAM_RETRY_BASE_DELAY_MS * attempt) / 1000)

    if last_response is not None:
        return last_response
    raise last_error


def _proxy_ai(endpoint: str, api_key: str, payload: dict, provider: str):
    return _request_with_retry(
        "POST",
        endpoint,
        operation=f"ai_{provider}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )


def _user_oauth_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def _google_access_token_info(token: str) -> dict:
    upstream = _request_with_retry(
        "GET",
        GOOGLE_TOKENINFO_ENDPOINT,
        operation="google_tokeninfo",
        retryable_statuses={500, 502, 503, 504},
        params={"access_token": token},
        timeout=10,
    )
    if not upstream.ok:
        raise ValueError("Google token verification failed")

    data = upstream.json()
    expires_in = data.get("expires_in")
    if expires_in is not None:
        try:
            if int(expires_in) <= 0:
                raise ValueError("Google OAuth token expired")
        except (TypeError, ValueError) as err:
            if str(err) == "Google OAuth token expired":
                raise
            raise ValueError("Google token verification returned invalid expiry") from err

    return data


def require_google_access_token(required_scopes: List[str] | None = None):
    required_scopes = required_scopes or []

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            token = _user_oauth_token()
            if not token:
                return _api_error("missing_google_token", "Missing Google OAuth bearer token", 401)

            try:
                token_info = _google_access_token_info(token)
            except ValueError as err:
                return _api_error("invalid_google_token", str(err), 401)
            except requests.exceptions.RequestException as err:
                return _api_error(
                    "google_token_verification_failed",
                    f"Google token verification failed: {err}",
                    502,
                    retryable=True,
                )

            actual_audiences = {
                token_info.get("issued_to", "").strip(),
                token_info.get("audience", "").strip(),
                token_info.get("azp", "").strip(),
            }
            actual_audiences.discard("")
            expected_audience = os.getenv("GOOGLE_CLIENT_ID", GOOGLE_CLIENT_ID).strip()
            if expected_audience and expected_audience not in actual_audiences:
                return _api_error("google_audience_mismatch", "Google OAuth token audience mismatch", 403)

            granted_scopes = {
                scope.strip() for scope in str(token_info.get("scope", "")).split(" ") if scope.strip()
            }
            missing_scopes = [scope for scope in required_scopes if scope not in granted_scopes]
            if missing_scopes:
                return _api_error(
                    "google_scope_missing",
                    "Google OAuth token missing required scopes",
                    403,
                    details={"missingScopes": missing_scopes},
                )

            request.google_token = token
            request.google_token_info = token_info
            return fn(*args, **kwargs)

        return wrapped

    return decorator


def _ai_cost_estimate(model: str, usage: dict | None) -> float:
    if not usage:
        return 0.0
    input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0
    pricing = {
        "gpt-4.1-mini": (0.40, 1.60),
        "kimi-k2-5": (0.15, 0.60),
    }
    input_rate, output_rate = pricing.get(model, (0.0, 0.0))
    return round(((input_tokens / 1_000_000) * input_rate) + ((output_tokens / 1_000_000) * output_rate), 6)


def _drive_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drive_list_files(token: str, query: str, *, page_size: int = 25):
    upstream = _request_with_retry(
        "GET",
        DRIVE_LIST_ENDPOINT,
        operation="drive_list",
        headers=_drive_headers(token),
        params={
            "q": query,
            "pageSize": min(page_size, 100),
            "fields": "files(id,name,createdTime,modifiedTime,size,appProperties,description)",
            "orderBy": "modifiedTime desc",
            "spaces": "appDataFolder",
        },
        timeout=30,
    )
    if not upstream.ok:
        raise RuntimeError(_parse_json_response(upstream))
    return _parse_json_response(upstream).get("files", [])


def _drive_get_file_content(token: str, file_id: str) -> dict:
    upstream = _request_with_retry(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        operation="drive_restore",
        headers=_drive_headers(token),
        params={"alt": "media"},
        timeout=30,
    )
    if not upstream.ok:
        raise RuntimeError(_parse_json_response(upstream))
    return _parse_json_response(upstream)


def _latest_session_version(token: str, session_id: str) -> dict | None:
    files = _drive_list_files(
        token,
        (
            "trashed=false and 'appDataFolder' in parents and "
            "appProperties has { key='recordType' and value='sheetAnalysisSession' } and "
            f"appProperties has {{ key='sessionId' and value='{session_id}' }}"
        ),
        page_size=10,
    )
    latest = None
    for file in files:
        app_properties = file.get("appProperties", {})
        try:
            version = int(app_properties.get("version", "0"))
        except ValueError:
            version = 0
        candidate = {"file": file, "version": version}
        if latest is None or candidate["version"] > latest["version"]:
            latest = candidate
    return latest


def _normalized_drive_session(body: dict, latest_version: int) -> dict:
    source_content = body.get("content", {}) if isinstance(body.get("content"), dict) else {}
    metadata = body.get("metadata", {}) if isinstance(body.get("metadata"), dict) else {}
    return {
        "schemaVersion": SESSION_SCHEMA_VERSION,
        "sessionId": body.get("sessionId") or str(uuid.uuid4()),
        "version": body.get("version"),
        "baseVersion": body.get("baseVersion", latest_version),
        "spreadsheetId": body.get("spreadsheetId") or source_content.get("spreadsheetId", ""),
        "range": body.get("range") or source_content.get("range", ""),
        "result": body.get("result", source_content.get("result")),
        "sheetData": body.get("sheetData", source_content.get("sheetData")),
        "metadata": {
            "task": body.get("task") or metadata.get("task", ""),
            "provider": body.get("provider") or metadata.get("provider", ""),
            "tags": metadata.get("tags", []),
            "spreadsheetName": metadata.get("spreadsheetName", ""),
        },
        "source": {
            "mode": body.get("sourceMode", "web"),
            "hostContext": body.get("hostContext", {}),
        },
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _session_filename(session: dict) -> str:
    return f"sheet-session-{session['sessionId']}-v{session['version']}.json"


@app.after_request
def add_security_headers(resp):
    request_id = getattr(request, "request_id", None)
    if request_id:
        resp.headers["X-Request-Id"] = request_id
    trace_id = getattr(request, "trace_id", None)
    if trace_id:
        resp.headers["X-Trace-Id"] = trace_id

    cors_origin = _cors_origin()
    if cors_origin:
        resp.headers["Access-Control-Allow-Origin"] = cors_origin
        resp.headers["Vary"] = "Origin"

    resp.headers["X-Content-Type-Options"] = "nosniff"
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
        f"frame-ancestors 'self' {' '.join(EMBED_ALLOWED_PARENTS)};"
    )
    if request.path.startswith("/api/"):
        duration_ms = round((time.time() - getattr(request, "started_at", time.time())) * 1000, 2)
        _record_metric(
            "api_request",
            path=request.path,
            method=request.method,
            status=resp.status_code,
            durationMs=duration_ms,
        )
    return resp


@app.before_request
def before_request_logging_and_rate_limit():
    request.request_id = _request_id()
    request.trace_id = _trace_id()
    request.started_at = time.time()
    key = f"{_client_ip()}:{request.path}"
    if request.path.startswith("/api/") and not rate_limiter.allow(key):
        _log("warn", "rate_limit_exceeded", request_id=request.request_id, path=request.path, ip=_client_ip())
        return _api_error("rate_limit_exceeded", "Rate limit exceeded", 429, retryable=True)


@app.route("/healthz", methods=["GET"])
def healthz():
    return _json_response({"ok": True, "environment": APP_ENV})


@app.route("/api/config", methods=["GET"])
def config():
    return _json_response(
        {
            "ok": True,
            "googleClientId": GOOGLE_CLIENT_ID,
            "defaultProvider": os.getenv("DEFAULT_AI_PROVIDER", "kimi"),
            "allowedProviders": ["kimi", "openai"],
            "environment": APP_ENV,
            "scopes": {
                "base": GOOGLE_SHEETS_SCOPE,
                "drive": f"{GOOGLE_DRIVE_SCOPE} {GOOGLE_DRIVE_APPDATA_SCOPE}",
            },
            "rateLimit": {
                "windowSeconds": RATE_LIMIT_WINDOW_SECONDS,
                "maxRequests": RATE_LIMIT_MAX_REQUESTS,
                "backend": rate_limiter.backend,
            },
            "retryPolicy": {
                "maxAttempts": UPSTREAM_MAX_ATTEMPTS,
                "baseDelayMs": UPSTREAM_RETRY_BASE_DELAY_MS,
                "retryableStatuses": sorted(UPSTREAM_RETRYABLE_STATUSES),
            },
            "features": {
                "sidebarBridge": True,
                "driveSessionVersioning": True,
                "observability": True,
            },
        }
    )


@app.route("/api/observability/summary", methods=["GET"])
def observability_summary():
    with _observability_lock:
        counters = dict(_observability["counters"])
        durations = dict(_observability["durationsMs"])
        recent_events = list(_observability["recentEvents"])
    return _json_response(
        {
            "ok": True,
            "environment": APP_ENV,
            "counters": counters,
            "durationsMs": durations,
            "recentEvents": recent_events,
        }
    )


@app.route("/api/events", methods=["POST"])
def ingest_event():
    invalid = _require_json()
    if invalid:
        return invalid

    body = request.get_json(silent=True) or {}
    event_name = str(body.get("eventName", "")).strip()
    if not event_name:
        return _api_error("event_name_required", "eventName is required", 400)

    _record_metric(
        "frontend_event",
        eventName=event_name,
        component=str(body.get("component", "frontend"))[:100],
        status=str(body.get("status", "info"))[:40],
        durationMs=float(body.get("durationMs", 0) or 0),
    )
    return _json_response({"ok": True, "accepted": True}, 202)


@app.route("/api/ai/kimi", methods=["POST"])
def proxy_kimi():
    invalid = _require_json()
    if invalid:
        return invalid

    payload = request.get_json(silent=True) or {}
    if "messages" not in payload:
        return _api_error("messages_required", "`messages` field is required", 400)

    api_key = _setting("KIMI_API_KEY", secret_fallback=True)
    if not api_key:
        return _api_error("server_secret_missing", "KIMI_API_KEY not configured on server", 500)

    try:
        upstream = _proxy_ai(KIMI_ENDPOINT, api_key, payload, "kimi")
        return _json_response({"ok": upstream.ok, "provider": "kimi", **_parse_json_response(upstream)}, upstream.status_code)
    except requests.exceptions.Timeout:
        return _api_error("ai_timeout", "AI service timeout", 504, retryable=True)
    except requests.exceptions.RequestException as err:
        return _api_error("ai_network_error", f"AI service network error: {err}", 502, retryable=True)


@app.route("/api/ai/openai", methods=["POST"])
def proxy_openai():
    invalid = _require_json()
    if invalid:
        return invalid

    payload = request.get_json(silent=True) or {}
    if "messages" not in payload:
        return _api_error("messages_required", "`messages` field is required", 400)

    api_key = _setting("OPENAI_API_KEY", secret_fallback=True)
    if not api_key:
        return _api_error("server_secret_missing", "OPENAI_API_KEY not configured on server", 500)

    try:
        upstream = _proxy_ai(OPENAI_ENDPOINT, api_key, payload, "openai")
        return _json_response({"ok": upstream.ok, "provider": "openai", **_parse_json_response(upstream)}, upstream.status_code)
    except requests.exceptions.Timeout:
        return _api_error("ai_timeout", "AI service timeout", 504, retryable=True)
    except requests.exceptions.RequestException as err:
        return _api_error("ai_network_error", f"AI service network error: {err}", 502, retryable=True)


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
            key = _setting("KIMI_API_KEY", secret_fallback=True)
            endpoint = KIMI_ENDPOINT
        elif provider == "openai":
            key = _setting("OPENAI_API_KEY", secret_fallback=True)
            endpoint = OPENAI_ENDPOINT
        else:
            errors.append({"provider": provider, "message": "Unsupported provider", "retryable": False})
            continue

        if not key:
            errors.append({"provider": provider, "message": "API key missing", "retryable": False})
            continue

        try:
            upstream = _proxy_ai(endpoint, key, payload.get("aiPayload", {}), provider)
            parsed = _parse_json_response(upstream)
            if upstream.ok:
                usage = parsed.get("usage", {})
                model = parsed.get("model", payload.get("aiPayload", {}).get("model", ""))
                _record_metric(
                    "ai_completion",
                    provider=provider,
                    model=model,
                    estimatedCostUsd=_ai_cost_estimate(model, usage),
                    promptTokens=usage.get("prompt_tokens", usage.get("input_tokens", 0)),
                    completionTokens=usage.get("completion_tokens", usage.get("output_tokens", 0)),
                )
                return _json_response(
                    {
                        "ok": True,
                        "provider": provider,
                        "attempts": errors,
                        **parsed,
                    },
                    200,
                )
            errors.append(
                {
                    "provider": provider,
                    "status": upstream.status_code,
                    "message": parsed.get("message", upstream.text[:500]),
                    "retryable": upstream.status_code in UPSTREAM_RETRYABLE_STATUSES,
                }
            )
        except requests.exceptions.RequestException as err:
            errors.append({"provider": provider, "message": str(err), "retryable": True})

    return _api_error(
        "all_providers_failed",
        "All providers failed",
        502,
        details={"attempts": errors},
        retryable=any(error.get("retryable") for error in errors),
    )


@app.route("/api/drive/save", methods=["POST"])
@require_google_access_token(required_scopes=[GOOGLE_DRIVE_SCOPE, GOOGLE_DRIVE_APPDATA_SCOPE])
def drive_save():
    invalid = _require_json()
    if invalid:
        return invalid

    token = request.google_token
    body = request.get_json(silent=True) or {}
    requested_session_id = body.get("sessionId") or str(uuid.uuid4())
    latest = _latest_session_version(token, requested_session_id)
    latest_version = latest["version"] if latest else 0
    session = _normalized_drive_session({**body, "sessionId": requested_session_id}, latest_version)
    requested_version = session.get("version")
    session["version"] = int(requested_version or (latest_version + 1))

    if latest and int(session.get("baseVersion", latest_version)) != latest_version:
        return _api_error(
            "drive_version_conflict",
            "Drive session version conflict",
            409,
            details={"sessionId": requested_session_id, "latestVersion": latest_version},
        )

    if latest and session["version"] <= latest_version:
        return _api_error(
            "drive_version_conflict",
            "Drive session version conflict",
            409,
            details={"sessionId": requested_session_id, "latestVersion": latest_version},
        )

    metadata = {
        "name": body.get("fileName") or _session_filename(session),
        "mimeType": "application/json",
        "parents": ["appDataFolder"] if body.get("useAppDataFolder", True) else [],
        "description": f"Sheet analysis session {session['sessionId']} v{session['version']}",
        "appProperties": {
            "recordType": "sheetAnalysisSession",
            "sessionId": session["sessionId"],
            "version": str(session["version"]),
            "schemaVersion": str(SESSION_SCHEMA_VERSION),
            "spreadsheetId": session.get("spreadsheetId", ""),
        },
    }

    files = {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "file": (_session_filename(session), json.dumps(session, ensure_ascii=False), "application/json"),
    }
    try:
        upstream = _request_with_retry(
            "POST",
            DRIVE_UPLOAD_ENDPOINT,
            operation="drive_save",
            headers=_drive_headers(token),
            files=files,
            timeout=60,
        )
        body_json = _parse_json_response(upstream)
        if upstream.ok:
            _record_metric(
                "drive_save",
                sessionId=session["sessionId"],
                version=session["version"],
                spreadsheetId=session.get("spreadsheetId", ""),
            )
            body_json.update(
                {
                    "ok": True,
                    "sessionId": session["sessionId"],
                    "version": session["version"],
                    "schemaVersion": SESSION_SCHEMA_VERSION,
                }
            )
            return _json_response(body_json, upstream.status_code)
        return _api_error(
            "drive_save_failed",
            "Drive save failed",
            upstream.status_code,
            details={"upstream": body_json},
            retryable=upstream.status_code in UPSTREAM_RETRYABLE_STATUSES,
        )
    except requests.exceptions.RequestException as err:
        return _api_error("drive_save_failed", f"Drive save failed: {err}", 502, retryable=True)


@app.route("/api/drive/list", methods=["GET"])
@require_google_access_token(required_scopes=[GOOGLE_DRIVE_SCOPE, GOOGLE_DRIVE_APPDATA_SCOPE])
def drive_list():
    token = request.google_token
    page_size = min(int(request.args.get("pageSize", "25")), 100)
    session_id = request.args.get("sessionId", "").strip()

    query = "trashed=false and 'appDataFolder' in parents"
    if session_id:
        query += (
            " and appProperties has { key='recordType' and value='sheetAnalysisSession' }"
            f" and appProperties has {{ key='sessionId' and value='{session_id}' }}"
        )

    try:
        files = _drive_list_files(token, query, page_size=page_size)
        normalized = []
        for file in files:
            app_properties = file.get("appProperties", {})
            normalized.append(
                {
                    "id": file.get("id"),
                    "name": file.get("name"),
                    "createdTime": file.get("createdTime"),
                    "modifiedTime": file.get("modifiedTime"),
                    "size": file.get("size"),
                    "sessionId": app_properties.get("sessionId"),
                    "version": int(app_properties.get("version", "0") or 0),
                    "schemaVersion": int(app_properties.get("schemaVersion", "0") or 0),
                    "spreadsheetId": app_properties.get("spreadsheetId", ""),
                    "description": file.get("description", ""),
                }
            )
        return _json_response({"ok": True, "files": normalized})
    except RuntimeError as err:
        return _api_error("drive_list_failed", "Drive list failed", 502, details={"upstream": str(err)}, retryable=True)
    except requests.exceptions.RequestException as err:
        return _api_error("drive_list_failed", f"Drive list failed: {err}", 502, retryable=True)


@app.route("/api/drive/restore", methods=["GET"])
@require_google_access_token(required_scopes=[GOOGLE_DRIVE_SCOPE, GOOGLE_DRIVE_APPDATA_SCOPE])
def drive_restore():
    token = request.google_token
    file_id = request.args.get("fileId", "").strip()
    session_id = request.args.get("sessionId", "").strip()
    requested_version = request.args.get("version", "").strip()

    try:
        if not file_id:
            if not session_id:
                return _api_error("restore_target_required", "fileId or sessionId is required", 400)
            latest = _latest_session_version(token, session_id)
            if latest is None:
                return _api_error("drive_session_not_found", "Drive session not found", 404)
            if requested_version and int(requested_version) != latest["version"]:
                files = _drive_list_files(
                    token,
                    (
                        "trashed=false and 'appDataFolder' in parents and "
                        "appProperties has { key='recordType' and value='sheetAnalysisSession' } and "
                        f"appProperties has {{ key='sessionId' and value='{session_id}' }} and "
                        f"appProperties has {{ key='version' and value='{requested_version}' }}"
                    ),
                    page_size=1,
                )
                if not files:
                    return _api_error("drive_session_not_found", "Drive session not found", 404)
                file_id = files[0]["id"]
            else:
                file_id = latest["file"]["id"]

        restored = _drive_get_file_content(token, file_id)
        _record_metric(
            "drive_restore",
            sessionId=restored.get("sessionId", ""),
            version=restored.get("version", 0),
            spreadsheetId=restored.get("spreadsheetId", ""),
        )
        return _json_response({"ok": True, "session": restored})
    except RuntimeError as err:
        return _api_error("drive_restore_failed", "Drive restore failed", 502, details={"upstream": str(err)}, retryable=True)
    except requests.exceptions.RequestException as err:
        return _api_error("drive_restore_failed", f"Drive restore failed: {err}", 502, retryable=True)


@app.route("/", defaults={"path": "index.html"})
@app.route("/<path:path>")
def static_proxy(path: str):
    if path.startswith("api/"):
        return _api_error("not_found", "Not found", 404)
    return send_from_directory(app.static_folder, path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
