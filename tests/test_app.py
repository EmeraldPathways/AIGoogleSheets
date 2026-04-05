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


def test_proxy_kimi_rejects_non_json():
    client = app_module.app.test_client()
    resp = client.post('/api/ai/kimi', data='x')
    assert resp.status_code == 400


def test_proxy_kimi_requires_messages(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    client = app_module.app.test_client()
    resp = client.post('/api/ai/kimi', json={})
    assert resp.status_code == 400


def test_proxy_kimi_success(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'test')
    fake_upstream = Mock()
    fake_upstream.status_code = 200
    fake_upstream.json.return_value = {'choices': [{'message': {'content': '{"ok": true}'}}]}

    with patch.object(app_module.requests, 'post', return_value=fake_upstream):
        client = app_module.app.test_client()
        resp = client.post('/api/ai/kimi', json={'messages': [{'role': 'user', 'content': 'hi'}]})

    assert resp.status_code == 200
    assert 'choices' in resp.get_json()


def test_ai_fallback_returns_second_provider(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'k')
    monkeypatch.setenv('OPENAI_API_KEY', 'o')

    failed = Mock()
    failed.ok = False
    failed.status_code = 500
    failed.text = 'kimi down'

    success = Mock()
    success.ok = True
    success.json.return_value = {'choices': [{'message': {'content': '{"done":true}'}}], 'model': 'gpt'}

    with patch.object(app_module.requests, 'post', side_effect=[failed, success]):
        client = app_module.app.test_client()
        resp = client.post('/api/ai/analyze', json={'aiPayload': {'messages': [{'role': 'user', 'content': 'x'}]}})

    assert resp.status_code == 200
    assert resp.get_json()['provider'] == 'openai'


def test_drive_requires_auth():
    client = app_module.app.test_client()
    resp = client.get('/api/drive/list')
    assert resp.status_code == 401


def test_rate_limiter_blocks_when_exceeded():
    limiter = app_module.InMemoryRateLimiter(window_seconds=60, max_requests=2)
    assert limiter.allow('a') is True
    assert limiter.allow('a') is True
    assert limiter.allow('a') is False
