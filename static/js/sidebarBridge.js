import { postEvent } from './api.js';

export function createSidebarBridge(store, onRestoreSession, onHostContext) {
  let hostContext = null;

  function isEmbedded() {
    return window.self !== window.top;
  }

  function syncFromHost(context) {
    hostContext = context || null;
    if (!hostContext) return;

    if (hostContext.spreadsheetId) {
      store.state.spreadsheetId = hostContext.spreadsheetId;
    }
    if (hostContext.activeRangeA1) {
      store.state.range = hostContext.activeSheetName
        ? `${hostContext.activeSheetName}!${hostContext.activeRangeA1}`
        : hostContext.activeRangeA1;
    }
  }

  function sendToHost(message) {
    if (!isEmbedded()) return;
    window.parent.postMessage({ source: 'aigs-webapp', ...message }, '*');
  }

  function init() {
    if (!isEmbedded()) return;

    window.addEventListener('message', (event) => {
      const data = event.data || {};
      if (data.source !== 'apps-script-sidebar') return;

      if (data.type === 'HOST_CONTEXT') {
        syncFromHost(data.context);
        if (onHostContext) onHostContext(data.context);
      }

      if (data.type === 'RESTORED_SESSION' && data.session) {
        onRestoreSession(data.session);
      }
    });

    sendToHost({ type: 'APP_READY' });
    sendToHost({ type: 'REQUEST_CONTEXT' });
    postEvent('sidebar_bridge_initialized', { component: 'sidebarBridge' });
  }

  return {
    init,
    isEmbedded,
    getHostContext: () => hostContext,
    requestContext: () => sendToHost({ type: 'REQUEST_CONTEXT' }),
    insertResult(result) {
      sendToHost({
        type: 'INSERT_RESULT',
        payload: {
          spreadsheetId: store.state.spreadsheetId,
          range: store.state.range,
          result,
        },
      });
    },
    restoreSession(session) {
      sendToHost({ type: 'RESTORE_SESSION', session });
    },
  };
}
