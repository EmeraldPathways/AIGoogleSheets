import { loadServerConfig, extractSpreadsheetId, hasDriveScope } from './config.js';
import { createStore, APP_STATES } from './state.js';
import { createAuthClient } from './auth.js';
import { readSheet, appendSheet } from './sheets.js';
import { analyzeWithFallback } from './ai/analyze.js';
import { saveSessionToDrive, listSavedSessions, restoreSessionFromDrive } from './drive.js';
import { postEvent } from './api.js';
import { createSidebarBridge } from './sidebarBridge.js';
import { setDisabled, setText, getValue } from './ui.js';

const store = createStore({
  appState: APP_STATES.UNAUTHENTICATED,
  accessToken: null,
  oauthScope: '',
  spreadsheetId: '',
  range: 'Sheet1!A1:Z200',
  sheetData: [],
  result: null,
  savedSessions: [],
  activeSessionId: '',
  latestSessionVersion: 0,
  config: null,
});

function errorText(err) {
  const requestRef = err?.requestId ? ` | requestId=${err.requestId}` : '';
  const attemptDetails = Array.isArray(err?.details?.attempts) && err.details.attempts.length
    ? ` | ${err.details.attempts.map((attempt) => {
      const status = attempt.status ? ` ${attempt.status}` : '';
      const message = attempt.message ? ` ${attempt.message}` : '';
      return `${attempt.provider}:${status}${message}`.trim();
    }).join(' ; ')}`
    : '';
  if (err?.message) return String(err.message) + requestRef + attemptDetails;
  if (typeof err === 'object') {
    try {
      return JSON.stringify(err) + requestRef + attemptDetails;
    } catch {
      return '[unserializable error object]' + requestRef + attemptDetails;
    }
  }
  return String(err) + requestRef + attemptDetails;
}

function syncUi(state) {
  const signedIn = Boolean(state.accessToken);
  const driveEnabled = hasDriveScope(state.oauthScope);
  const embedded = document.body.dataset.embedded === 'true';

  setText('auth-status', signedIn ? `Signed in. Scope: ${state.oauthScope}` : 'Not signed in.');
  setText('session-status', state.activeSessionId ? `Session ${state.activeSessionId} v${state.latestSessionVersion}` : 'No saved Drive session yet.');
  setDisabled('sign-in-btn', signedIn);
  setDisabled('sign-in-drive-btn', !signedIn || driveEnabled);
  setDisabled('sign-out-btn', !signedIn);
  setDisabled('load-sheet-btn', !signedIn);
  setDisabled('analyze-btn', !signedIn || state.sheetData.length === 0 || state.appState === APP_STATES.ANALYZING);
  setDisabled('save-drive-btn', !signedIn || !state.result || !driveEnabled);
  setDisabled('list-drive-btn', !signedIn || !driveEnabled);
  setDisabled('restore-drive-btn', !signedIn || !driveEnabled);
  setDisabled('sync-sidebar-btn', !embedded);
  setDisabled('insert-result-btn', !embedded || !state.result);
}

function renderDriveSessions(items) {
  if (!items.length) {
    setText('drive-output', 'No Drive sessions yet.');
    return;
  }

  const formatted = items.map((item) => (
    `${item.name} | session=${item.sessionId || 'n/a'} | version=${item.version || 0} | modified=${item.modifiedTime || 'n/a'}`
  ));
  setText('drive-output', formatted.join('\n'));
}

function restoreSessionIntoStore(session) {
  store.state.activeSessionId = session.sessionId || '';
  store.state.latestSessionVersion = session.version || 0;
  store.state.spreadsheetId = session.spreadsheetId || store.state.spreadsheetId;
  store.state.range = session.range || store.state.range;
  store.state.result = session.result || null;
  store.state.sheetData = session.sheetData || store.state.sheetData;
  if (session.result) {
    setText('results-output', JSON.stringify(session.result, null, 2));
  }
  if (session.range) {
    const rangeInput = document.getElementById('range-input');
    if (rangeInput) rangeInput.value = session.range;
  }
  if (session.spreadsheetId) {
    const spreadsheetInput = document.getElementById('spreadsheet-input');
    if (spreadsheetInput) spreadsheetInput.value = session.spreadsheetId;
  }
}

function syncSheetInputsFromState() {
  const spreadsheetInput = document.getElementById('spreadsheet-input');
  const rangeInput = document.getElementById('range-input');

  if (spreadsheetInput && store.state.spreadsheetId) {
    spreadsheetInput.value = store.state.spreadsheetId;
  }

  if (rangeInput && store.state.range) {
    rangeInput.value = store.state.range;
  }
}

async function init() {
  const cfg = await loadServerConfig();
  store.state.config = cfg;
  const sidebarBridge = createSidebarBridge(
    store,
    restoreSessionIntoStore,
    (context) => {
      syncSheetInputsFromState();
      setText(
        'host-output',
        context
          ? `Connected to ${context.spreadsheetName} | ${context.activeSheetName} | ${context.activeRangeA1 || 'No range'}`
          : 'No sidebar context available.',
      );
    },
    (payload) => {
      store.state.spreadsheetId = payload.spreadsheetId || store.state.spreadsheetId;
      store.state.range = payload.range || store.state.range;
      store.state.sheetData = Array.isArray(payload.values) ? payload.values : [];
      if (store.state.sheetData.length > 0) {
        store.state.appState = APP_STATES.DATA_LOADED;
        setText('results-output', `Loaded ${Math.max(store.state.sheetData.length - 1, 0)} data rows from current sheet.`);
      }
      syncSheetInputsFromState();
    },
  );
  document.body.dataset.embedded = String(sidebarBridge.isEmbedded());

  const auth = createAuthClient(cfg.googleClientId, cfg.scopes, (token, scope) => {
    store.state.accessToken = token;
    store.state.oauthScope = scope;
    store.state.appState = token ? APP_STATES.AUTHENTICATED : APP_STATES.UNAUTHENTICATED;
  });

  auth.init();
  sidebarBridge.init();
  syncSheetInputsFromState();

  document.getElementById('sign-in-btn').addEventListener('click', () => auth.signIn());
  document.getElementById('sign-in-drive-btn').addEventListener('click', () => auth.requestDriveScope());
  document.getElementById('sign-out-btn').addEventListener('click', () => auth.signOut());

  document.getElementById('sync-sidebar-btn').addEventListener('click', () => {
    sidebarBridge.requestContext();
    sidebarBridge.requestSheetData();
    setText('host-output', 'Requested fresh sheet context from sidebar host.');
  });

  document.getElementById('insert-result-btn').addEventListener('click', () => {
    if (!store.state.result) return;
    sidebarBridge.insertResult(store.state.result);
    setText('host-output', 'Sent current analysis result to sidebar host.');
  });

  document.getElementById('load-sheet-btn').addEventListener('click', async () => {
    try {
      setText('results-output', 'Loading sheet data...');

      const rawSheet = getValue('spreadsheet-input') || store.state.spreadsheetId;
      const spreadsheetId = extractSpreadsheetId(rawSheet);
      if (!spreadsheetId) throw new Error('Invalid spreadsheet ID or URL.');

      const range = getValue('range-input') || store.state.range || 'Sheet1!A1:Z200';
      const values = await readSheet(spreadsheetId, range, store.state.accessToken);
      store.state.sheetData = values;
      store.state.spreadsheetId = spreadsheetId;
      store.state.range = range;
      store.state.appState = APP_STATES.DATA_LOADED;
      setText('results-output', `Loaded ${Math.max(values.length - 1, 0)} data rows.`);
    } catch (err) {
      store.state.appState = APP_STATES.ERROR;
      setText('results-output', errorText(err));
    }
  });

  document.getElementById('analyze-btn').addEventListener('click', async () => {
    try {
      store.state.appState = APP_STATES.ANALYZING;
      setText('results-output', 'Analyzing...');

      const providerName = getValue('provider-select');
      const task = getValue('task-select') || 'summarize';
      const result = await analyzeWithFallback(store.state.sheetData, task, providerName, cfg.retryPolicy || {});

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
      setText('results-output', errorText(err));
    }
  });

  document.getElementById('save-drive-btn').addEventListener('click', async () => {
    try {
      const payload = {
        sessionId: store.state.activeSessionId || undefined,
        baseVersion: store.state.latestSessionVersion,
        spreadsheetId: store.state.spreadsheetId,
        range: store.state.range,
        result: store.state.result,
        sheetData: store.state.sheetData,
        metadata: {
          task: getValue('task-select') || 'summarize',
          provider: store.state.result?.provider || getValue('provider-select'),
        },
        hostContext: sidebarBridge.getHostContext() || {},
        sourceMode: sidebarBridge.isEmbedded() ? 'apps_script_sidebar' : 'web',
        useAppDataFolder: true,
      };
      const saved = await saveSessionToDrive(store.state.accessToken, payload);
      store.state.activeSessionId = saved.sessionId;
      store.state.latestSessionVersion = saved.version;
      setText('drive-output', `Saved session ${saved.sessionId} version ${saved.version}.`);
    } catch (err) {
      setText('drive-output', errorText(err));
    }
  });

  document.getElementById('list-drive-btn').addEventListener('click', async () => {
    try {
      const items = await listSavedSessions(store.state.accessToken, store.state.activeSessionId);
      store.state.savedSessions = items;
      renderDriveSessions(items);
    } catch (err) {
      setText('drive-output', errorText(err));
    }
  });

  document.getElementById('restore-drive-btn').addEventListener('click', async () => {
    try {
      const fileId = getValue('restore-file-id');
      const sessionId = getValue('restore-session-id') || store.state.activeSessionId;
      const version = getValue('restore-version');
      const session = await restoreSessionFromDrive(store.state.accessToken, {
        fileId: fileId || undefined,
        sessionId: sessionId || undefined,
        version: version || undefined,
      });
      restoreSessionIntoStore(session);
      setText('drive-output', `Restored session ${session.sessionId} version ${session.version}.`);
      sidebarBridge.restoreSession(session);
    } catch (err) {
      setText('drive-output', errorText(err));
    }
  });

  syncUi(store.state);
  store.subscribe(syncUi);
  postEvent('app_initialized', { component: 'main', status: 'success' });
}

init().catch((err) => {
  setText('results-output', `Initialization failed: ${errorText(err)}`);
});
