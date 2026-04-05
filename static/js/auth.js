export function createAuthClient(clientId, scopes, onTokenUpdate) {
  let tokenClient;
  let accessToken = null;
  let currentScope = scopes.base;

  function init() {
    if (!window.google?.accounts?.oauth2) {
      throw new Error('GIS library not available');
    }

    tokenClient = window.google.accounts.oauth2.initTokenClient({
      client_id: clientId,
      scope: currentScope,
      callback: (response) => {
        accessToken = response.access_token;
        onTokenUpdate(accessToken, currentScope);
      },
      error_callback: () => {
        throw new Error('Google sign-in failed');
      },
    });
  }

  function signIn() {
    currentScope = scopes.base;
    tokenClient.requestAccessToken({ prompt: 'consent', scope: currentScope });
  }

  function requestDriveScope() {
    currentScope = `${scopes.base} ${scopes.drive}`;
    tokenClient.requestAccessToken({ prompt: 'consent', scope: currentScope });
  }

  function signOut() {
    accessToken = null;
    onTokenUpdate(null, scopes.base);
  }

  return {
    init,
    signIn,
    requestDriveScope,
    signOut,
    getAccessToken: () => accessToken,
    getScope: () => currentScope,
  };
}
