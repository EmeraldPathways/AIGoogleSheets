import { fetchJson } from './api.js';

function driveRetryPolicy() {
  return {
    maxAttempts: 3,
    baseDelayMs: 250,
    retryableStatuses: [429, 500, 502, 503, 504],
  };
}

export async function saveSessionToDrive(accessToken, payload) {
  return fetchJson(
    '/api/drive/save',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(payload),
    },
    driveRetryPolicy(),
  );
}

export async function listSavedSessions(accessToken, sessionId = '') {
  const suffix = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : '';
  const data = await fetchJson(
    `/api/drive/list${suffix}`,
    {
      headers: { Authorization: `Bearer ${accessToken}` },
    },
    driveRetryPolicy(),
  );
  return data.files || [];
}

export async function restoreSessionFromDrive(accessToken, params = {}) {
  const query = new URLSearchParams();
  if (params.fileId) query.set('fileId', params.fileId);
  if (params.sessionId) query.set('sessionId', params.sessionId);
  if (params.version) query.set('version', String(params.version));
  const data = await fetchJson(
    `/api/drive/restore?${query.toString()}`,
    {
      headers: { Authorization: `Bearer ${accessToken}` },
    },
    driveRetryPolicy(),
  );
  return data.session;
}
