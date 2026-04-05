import { fetchJson } from './api.js';

export async function loadServerConfig() {
  return fetchJson('/api/config', {}, { maxAttempts: 2, baseDelayMs: 150 });
}

export function extractSpreadsheetId(rawInput) {
  if (!rawInput) return '';
  const input = rawInput.trim();
  if (/^[a-zA-Z0-9-_]{20,}$/.test(input)) return input;
  const match = input.match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  return match?.[1] || '';
}

export function hasDriveScope(scope = '') {
  return scope.includes('drive.file') || scope.includes('drive');
}
