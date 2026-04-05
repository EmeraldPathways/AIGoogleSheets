import { loadServerConfig, extractSpreadsheetId, hasDriveScope } from './config.js';
import { createStore, APP_STATES } from './state.js';
import { createAuthClient } from './auth.js';
import { readSheet, appendSheet } from './sheets.js';
import { analyzeWithFallback } from './ai/analyze.js';
import { saveSessionToDrive, listSavedSessions } from './drive.js';
import { setDisabled, setText, getValue } from './ui.js';

const store = createStore({
  appState: APP_STATES.UNAUTHENTICATED,
  accessToken: null,
  oauthScope: '',
  spreadsheetId: '',
  range: 'Sheet1!A1:Z200',
  sheetData: [],
  result: null,
});

function syncUi(state) {
  const signedIn = Boolean(state.accessToken);
  const driveEnabled = hasDriveScope(state.oauthScope);

  setText('auth-status', signedIn ? `Signed in. Scope: ${state.oauthScope}` : 'Not signed in.');
  setDisabled('sign-in-btn', signedIn);
  setDisabled('sign-in-drive-btn', !signedIn || driveEnabled);
  setDisabled('sign-out-btn', !signedIn);
  setDisabled('load-sheet-btn', !signedIn);
  setDisabled('analyze-btn', !signedIn || state.sheetData.length === 0 || state.appState === APP_STATES.ANALYZING);
  setDisabled('save-drive-btn', !signedIn || !state.result || !driveEnabled);
  setDisabled('list-drive-btn', !signedIn || !driveEnabled);
}

store.subscribe(syncUi);

async function init() {
  const cfg = await loadServerConfig();
  const auth = createAuthClient(cfg.googleClientId, cfg.scopes, (token, scope) => {
    store.state.accessToken = token;
    store.state.oauthScope = scope;
    store.state.appState = token ? APP_STATES.AUTHENTICATED : APP_STATES.UNAUTHENTICATED;
  });

  auth.init();

  document.getElementById('sign-in-btn').addEventListener('click', () => auth.signIn());
  document.getElementById('sign-in-drive-btn').addEventListener('click', () => auth.requestDriveScope());
  document.getElementById('sign-out-btn').addEventListener('click', () => auth.signOut());

  document.getElementById('load-sheet-btn').addEventListener('click', async () => {
    try {
      const rawSheet = getValue('spreadsheet-input');
      const spreadsheetId = extractSpreadsheetId(rawSheet);
      if (!spreadsheetId) throw new Error('Invalid spreadsheet ID or URL.');

      const range = getValue('range-input') || 'Sheet1!A1:Z200';
      const values = await readSheet(spreadsheetId, range, store.state.accessToken);
      store.state.sheetData = values;
      store.state.spreadsheetId = spreadsheetId;
      store.state.range = range;
      store.state.appState = APP_STATES.DATA_LOADED;
      setText('results-output', `Loaded ${Math.max(values.length - 1, 0)} data rows.`);
    } catch (err) {
      store.state.appState = APP_STATES.ERROR;
      setText('results-output', String(err.message || err));
    }
  });

  document.getElementById('analyze-btn').addEventListener('click', async () => {
    try {
      store.state.appState = APP_STATES.ANALYZING;
      setText('results-output', 'Analyzing...');

      const providerName = getValue('provider-select');
      const task = getValue('task-select') || 'summarize';
      const result = await analyzeWithFallback(store.state.sheetData, task, providerName);

      store.state.result = result;
      store.state.appState = APP_STATES.DATA_LOADED;
      setText('results-output', JSON.stringify(result, null, 2));

      await appendSheet(
        store.state.spreadsheetId,
        'AI Logs!A1',
        [[new Date().toISOString(), result.provider || providerName, task, result.model || '', 'ok']],
        store.state.accessToken,
      );
    } catch (err) {
      store.state.appState = APP_STATES.ERROR;
      setText('results-output', String(err.message || err));
    }
  });

  document.getElementById('save-drive-btn').addEventListener('click', async () => {
    try {
      const payload = {
        fileName: `sheet-analysis-${Date.now()}.json`,
        content: {
          spreadsheetId: store.state.spreadsheetId,
          range: store.state.range,
          result: store.state.result,
          createdAt: new Date().toISOString(),
        },
        useAppDataFolder: true,
      };
      const saved = await saveSessionToDrive(store.state.accessToken, payload);
      setText('drive-output', `Saved to Drive file id: ${saved.id}`);
    } catch (err) {
      setText('drive-output', String(err.message || err));
    }
  });

  document.getElementById('list-drive-btn').addEventListener('click', async () => {
    try {
      const items = await listSavedSessions(store.state.accessToken);
      setText('drive-output', JSON.stringify(items, null, 2));
    } catch (err) {
      setText('drive-output', String(err.message || err));
    }
  });

  syncUi(store.state);
}

init().catch((err) => {
  setText('results-output', `Initialization failed: ${err.message || err}`);
});
