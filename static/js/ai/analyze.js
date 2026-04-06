import { fetchJson } from '../api.js';

export async function analyzeWithFallback(sheetData, task, preferredProvider = 'kimi', retryPolicy = {}) {
  const aiPayload = {
    model: preferredProvider === 'openai' ? 'gpt-4o-mini' : 'kimi-k2-5',
    messages: [
      { role: 'system', content: `You are analyzing spreadsheet data. Task: ${task}. Return JSON.` },
      { role: 'user', content: JSON.stringify(sheetData) },
    ],
    temperature: 0.3,
    response_format: { type: 'json_object' },
  };

  const providerOrder = preferredProvider === 'openai' ? ['openai', 'kimi'] : ['kimi', 'openai'];
  const data = await fetchJson(
    '/api/ai/analyze',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sheetData, aiPayload, providerOrder }),
    },
    {
      maxAttempts: retryPolicy.maxAttempts || 3,
      baseDelayMs: retryPolicy.baseDelayMs || 300,
      retryableStatuses: retryPolicy.retryableStatuses || [429, 500, 502, 503, 504],
    },
  );

  return {
    provider: data.provider,
    model: data.model,
    usage: data.usage,
    content: data?.choices?.[0]?.message?.content ?? '',
  };
}
