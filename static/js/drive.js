export async function saveSessionToDrive(accessToken, payload) {
  const res = await fetch('/api/drive/save', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Drive save failed');
  return data;
}

export async function listSavedSessions(accessToken) {
  const res = await fetch('/api/drive/list', {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Drive list failed');
  return data.files || [];
}
