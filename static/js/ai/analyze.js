import { fetchJson } from '../api.js';

function parseStructuredContent(content) {
  if (typeof content !== 'string') return content;

  const trimmed = content.trim();
  if (!trimmed) return content;

  try {
    return JSON.parse(trimmed);
  } catch {
    return content;
  }
}

export async function analyzeWithFallback(sheetData, task, preferredProvider = 'kimi', retryPolicy = {}) {
  const isOpenAI = preferredProvider === 'openai';
  const aiPayload = {
    model: isOpenAI ? 'gpt-4o-mini' : 'kimi-k2-turbo-preview',
    messages: [
      { role: 'system', content: `You are analyzing spreadsheet data. Task: ${task}. Return JSON.` },
      { role: 'user', content: JSON.stringify(sheetData) },
    ],
    temperature: isOpenAI ? 0.3 : 0.3,
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

  const rawContent = data?.choices?.[0]?.message?.content ?? '';

  return {
    provider: data.provider,
    model: data.model,
    usage: data.usage,
    content: parseStructuredContent(rawContent),
    rawContent,
  };
}
