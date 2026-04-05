export async function loadServerConfig() {
  const res = await fetch('/api/config');
  if (!res.ok) throw new Error('Unable to load runtime config');
  return res.json();
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
