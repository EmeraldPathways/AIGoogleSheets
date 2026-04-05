from pathlib import Path
from unittest.mock import Mock, patch
import importlib.util

spec = importlib.util.spec_from_file_location('app_module', Path(__file__).resolve().parents[1] / 'app.py')
app_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_module)


def test_healthz():
    client = app_module.app.test_client()
    resp = client.get('/healthz')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['requestId']
    assert body['traceId']


def test_config_includes_retry_and_rate_limit_backend():
    client = app_module.app.test_client()
    resp = client.get('/api/config')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['retryPolicy']['maxAttempts'] >= 1
    assert body['rateLimit']['backend'] in {'memory', 'redis'}


def test_proxy_kimi_rejects_non_json():
    client = app_module.app.test_client()
    resp = client.post('/api/ai/kimi', data='x')
    assert resp.status_code == 400
    assert resp.get_json()['error']['code'] == 'json_required'


def test_proxy_kimi_requires_messages(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    client = app_module.app.test_client()
    resp = client.post('/api/ai/kimi', json={})
    assert resp.status_code == 400
    assert resp.get_json()['error']['code'] == 'messages_required'


def test_proxy_kimi_success(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    fake_upstream = Mock()
    fake_upstream.ok = True
    fake_upstream.status_code = 200
    fake_upstream.json.return_value = {'choices': [{'message': {'content': '{"ok": true}'}}]}

    with patch.object(app_module, '_proxy_ai', return_value=fake_upstream):
        client = app_module.app.test_client()
        resp = client.post('/api/ai/kimi', json={'messages': [{'role': 'user', 'content': 'hi'}]})

    assert resp.status_code == 200
    assert resp.get_json()['provider'] == 'kimi'


def test_ai_fallback_returns_second_provider(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'k')
    monkeypatch.setenv('OPENAI_API_KEY', 'o')

    failed = Mock()
    failed.ok = False
    failed.status_code = 500
    failed.text = 'kimi down'
    failed.json.return_value = {'message': 'kimi down'}

    success = Mock()
    success.ok = True
    success.status_code = 200
    success.json.return_value = {'choices': [{'message': {'content': '{"done":true}'}}], 'model': 'gpt-4.1-mini'}

    with patch.object(app_module, '_proxy_ai', side_effect=[failed, success]):
        client = app_module.app.test_client()
        resp = client.post('/api/ai/analyze', json={'aiPayload': {'messages': [{'role': 'user', 'content': 'x'}]}})

    assert resp.status_code == 200
    assert resp.get_json()['provider'] == 'openai'


def test_drive_requires_auth():
    client = app_module.app.test_client()
    resp = client.get('/api/drive/list')
    assert resp.status_code == 401
    assert resp.get_json()['error']['code'] == 'missing_google_token'


def test_drive_rejects_invalid_google_token():
    tokeninfo = Mock()
    tokeninfo.ok = False
    tokeninfo.json.return_value = {'error': 'invalid_token'}

    with patch.object(app_module, '_request_with_retry', return_value=tokeninfo):
        client = app_module.app.test_client()
        resp = client.get('/api/drive/list', headers={'Authorization': 'Bearer bad-token'})

    assert resp.status_code == 401
    assert resp.get_json()['error']['message'] == 'Google token verification failed'


def test_drive_rejects_audience_mismatch(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'expected-client-id')

    tokeninfo = Mock()
    tokeninfo.ok = True
    tokeninfo.json.return_value = {
        'issued_to': 'different-client-id',
        'scope': 'https://www.googleapis.com/auth/drive.file',
        'expires_in': '3599',
    }

    with patch.object(app_module, '_request_with_retry', return_value=tokeninfo):
        client = app_module.app.test_client()
        resp = client.get('/api/drive/list', headers={'Authorization': 'Bearer token'})

    assert resp.status_code == 403
    assert resp.get_json()['error']['code'] == 'google_audience_mismatch'


def test_drive_rejects_missing_scope(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'expected-client-id')

    tokeninfo = Mock()
    tokeninfo.ok = True
    tokeninfo.json.return_value = {
        'issued_to': 'expected-client-id',
        'scope': 'https://www.googleapis.com/auth/spreadsheets',
        'expires_in': '3599',
    }

    with patch.object(app_module, '_request_with_retry', return_value=tokeninfo):
        client = app_module.app.test_client()
        resp = client.get('/api/drive/list', headers={'Authorization': 'Bearer token'})

    assert resp.status_code == 403
    body = resp.get_json()
    assert body['error']['code'] == 'google_scope_missing'
    assert body['error']['details']['missingScopes'] == ['https://www.googleapis.com/auth/drive.file']


def test_drive_list_verifies_token_then_calls_drive(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'expected-client-id')

    tokeninfo = Mock()
    tokeninfo.ok = True
    tokeninfo.json.return_value = {
        'issued_to': 'expected-client-id',
        'scope': 'https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file',
        'expires_in': '3599',
    }

    with patch.object(app_module, '_request_with_retry', return_value=tokeninfo):
        with patch.object(app_module, '_drive_list_files', return_value=[{
            'id': '1',
            'name': 'session.json',
            'modifiedTime': '2026-04-05T12:00:00Z',
            'appProperties': {'sessionId': 'abc', 'version': '2', 'schemaVersion': '2', 'spreadsheetId': 'sheet-1'},
        }]):
            client = app_module.app.test_client()
            resp = client.get('/api/drive/list?pageSize=10', headers={'Authorization': 'Bearer token'})

    assert resp.status_code == 200
    assert resp.get_json()['files'][0]['sessionId'] == 'abc'


def test_drive_save_verifies_token_before_upload(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'expected-client-id')

    tokeninfo = Mock()
    tokeninfo.ok = True
    tokeninfo.json.return_value = {
        'issued_to': 'expected-client-id',
        'scope': 'https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file',
        'expires_in': '3599',
    }

    upload_response = Mock()
    upload_response.ok = True
    upload_response.status_code = 200
    upload_response.json.return_value = {'id': 'file-123'}

    with patch.object(app_module, '_request_with_retry', side_effect=[tokeninfo, upload_response]):
        with patch.object(app_module, '_latest_session_version', return_value=None):
            client = app_module.app.test_client()
            resp = client.post(
                '/api/drive/save',
                headers={'Authorization': 'Bearer token'},
                json={'fileName': 'analysis.json', 'content': {'ok': True}},
            )

    assert resp.status_code == 200
    assert resp.get_json()['id'] == 'file-123'
    assert resp.get_json()['version'] == 1


def test_drive_save_rejects_version_conflict(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'expected-client-id')

    tokeninfo = Mock()
    tokeninfo.ok = True
    tokeninfo.json.return_value = {
        'issued_to': 'expected-client-id',
        'scope': 'https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file',
        'expires_in': '3599',
    }

    with patch.object(app_module, '_request_with_retry', return_value=tokeninfo):
        with patch.object(app_module, '_latest_session_version', return_value={'version': 3, 'file': {'id': 'f1'}}):
            client = app_module.app.test_client()
            resp = client.post(
                '/api/drive/save',
                headers={'Authorization': 'Bearer token'},
                json={'sessionId': 'session-1', 'baseVersion': 1, 'result': {'ok': True}},
            )

    assert resp.status_code == 409
    assert resp.get_json()['error']['code'] == 'drive_version_conflict'


def test_drive_restore_by_session(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'expected-client-id')

    tokeninfo = Mock()
    tokeninfo.ok = True
    tokeninfo.json.return_value = {
        'issued_to': 'expected-client-id',
        'scope': 'https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file',
        'expires_in': '3599',
    }

    with patch.object(app_module, '_request_with_retry', return_value=tokeninfo):
        with patch.object(app_module, '_latest_session_version', return_value={'version': 2, 'file': {'id': 'file-1'}}):
            with patch.object(app_module, '_drive_get_file_content', return_value={'sessionId': 'session-1', 'version': 2, 'result': {'ok': True}}):
                client = app_module.app.test_client()
                resp = client.get('/api/drive/restore?sessionId=session-1', headers={'Authorization': 'Bearer token'})

    assert resp.status_code == 200
    assert resp.get_json()['session']['version'] == 2


def test_rate_limiter_blocks_when_exceeded():
    limiter = app_module.InMemoryRateLimiter(window_seconds=60, max_requests=2)
    assert limiter.allow('a') is True
    assert limiter.allow('a') is True
    assert limiter.allow('a') is False
