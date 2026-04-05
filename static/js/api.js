function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function normalizeApiError(payload, fallbackMessage = 'Request failed') {
  const error = payload?.error;
  if (error && typeof error === 'object') {
    const wrapped = new Error(error.message || fallbackMessage);
    wrapped.code = error.code || 'api_error';
    wrapped.details = error.details || {};
    wrapped.retryable = Boolean(error.retryable);
    wrapped.requestId = payload?.requestId || '';
    wrapped.traceId = payload?.traceId || '';
    return wrapped;
  }

  if (typeof payload?.error === 'string') {
    const wrapped = new Error(payload.error);
    wrapped.code = 'api_error';
    wrapped.details = {};
    wrapped.retryable = false;
    wrapped.requestId = payload?.requestId || '';
    wrapped.traceId = payload?.traceId || '';
    return wrapped;
  }

  return new Error(payload?.message || fallbackMessage);
}

export async function retryAsync(fn, policy = {}) {
  const maxAttempts = policy.maxAttempts || 1;
  const baseDelayMs = policy.baseDelayMs || 250;
  let lastError;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fn(attempt);
    } catch (error) {
      lastError = error;
      if (attempt >= maxAttempts || error?.retryable === false) {
        throw error;
      }
      await delay(baseDelayMs * attempt);
    }
  }

  throw lastError;
}

export async function fetchJson(url, options = {}, retryPolicy = {}) {
  return retryAsync(async () => {
    const response = await fetch(url, options);
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }

    if (!response.ok) {
      const error = normalizeApiError(payload, `Request failed: ${response.status}`);
      if (retryPolicy.retryableStatuses?.includes(response.status) && error.retryable !== false) {
        error.retryable = true;
      }
      throw error;
    }

    return payload;
  }, retryPolicy);
}

export async function postEvent(eventName, payload = {}) {
  try {
    await fetchJson(
      '/api/events',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ eventName, ...payload }),
      },
      { maxAttempts: 1 },
    );
  } catch {
    // Observability events should never break user flows.
  }
}
