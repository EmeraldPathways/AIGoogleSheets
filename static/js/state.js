export const APP_STATES = {
  UNAUTHENTICATED: 'UNAUTHENTICATED',
  AUTHENTICATED: 'AUTHENTICATED',
  DATA_LOADED: 'DATA_LOADED',
  ANALYZING: 'ANALYZING',
  ERROR: 'ERROR',
};

export function createStore(initial) {
  const listeners = new Set();
  const state = new Proxy(initial, {
    set(target, prop, value) {
      target[prop] = value;
      listeners.forEach((fn) => fn(target));
      return true;
    },
  });

  return {
    state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
