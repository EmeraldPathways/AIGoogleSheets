import { postEvent, retryAsync } from './api.js';

function gapiReady() {
  return new Promise((resolve, reject) => {
    if (!window.gapi) {
      reject(new Error('gapi not loaded'));
      return;
    }
    window.gapi.load('client', async () => {
      try {
        await window.gapi.client.init({
          discoveryDocs: ['https://sheets.googleapis.com/$discovery/rest?version=v4'],
        });
        resolve();
      } catch (err) {
        reject(err);
      }
    });
  });
}

async function readSheetViaFetch(spreadsheetId, range, accessToken) {
  const params = new URLSearchParams({ majorDimension: 'ROWS' });
  const response = await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${encodeURIComponent(spreadsheetId)}/values/${encodeURIComponent(range)}?${params.toString()}`,
    {
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    },
  );

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload?.error?.message || `Sheets read failed: ${response.status}`);
  }

  return payload.values || [];
}

export async function readSheet(spreadsheetId, range, accessToken) {
  const started = performance.now();
  const result = await retryAsync(async () => {
    try {
      await gapiReady();
      window.gapi.client.setToken({ access_token: accessToken });
      const res = await window.gapi.client.sheets.spreadsheets.values.get({
        spreadsheetId,
        range,
      });
      return res.result.values || [];
    } catch (err) {
      return readSheetViaFetch(spreadsheetId, range, accessToken);
    }
  }, {
    maxAttempts: 2,
    baseDelayMs: 200,
  });
  postEvent('sheet_read', {
    component: 'sheets',
    durationMs: Math.round(performance.now() - started),
    status: 'success',
  });
  return result;
}

export async function appendSheet(spreadsheetId, range, values, accessToken) {
  const started = performance.now();
  const result = await retryAsync(async () => {
    await gapiReady();
    window.gapi.client.setToken({ access_token: accessToken });
    return window.gapi.client.sheets.spreadsheets.values.append({
      spreadsheetId,
      range,
      valueInputOption: 'RAW',
      insertDataOption: 'INSERT_ROWS',
      resource: { values },
    });
  }, {
    maxAttempts: 2,
    baseDelayMs: 200,
  });
  postEvent('sheet_append', {
    component: 'sheets',
    durationMs: Math.round(performance.now() - started),
    status: 'success',
  });
  return result;
}
