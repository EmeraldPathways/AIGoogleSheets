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

export async function readSheet(spreadsheetId, range, accessToken) {
  await gapiReady();
  window.gapi.client.setToken({ access_token: accessToken });
  const res = await window.gapi.client.sheets.spreadsheets.values.get({
    spreadsheetId,
    range,
  });
  return res.result.values || [];
}

export async function appendSheet(spreadsheetId, range, values, accessToken) {
  await gapiReady();
  window.gapi.client.setToken({ access_token: accessToken });
  return window.gapi.client.sheets.spreadsheets.values.append({
    spreadsheetId,
    range,
    valueInputOption: 'RAW',
    insertDataOption: 'INSERT_ROWS',
    resource: { values },
  });
}
