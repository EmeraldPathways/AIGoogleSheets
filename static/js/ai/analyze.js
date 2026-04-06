import { fetchJson } from '../api.js';

function normalizeContent(content) {
  if (typeof content !== 'string') {
    return content == null ? '' : String(content);
  }
  return content.trim();
}

export async function analyzeWithFallback(sheetData, task, preferredProvider = 'kimi', retryPolicy = {}) {
  const isOpenAI = preferredProvider === 'openai';
  const aiPayload = {
    model: isOpenAI ? 'gpt-4o-mini' : 'kimi-k2-turbo-preview',
    messages: [
      {
        role: 'system',
        content: `You analyze spreadsheet data. Task: ${task}. Return only a concise plain-text report with short headings and bullet points. Do not return JSON, markdown code fences, or explanatory preamble.`,
      },
      { role: 'user', content: JSON.stringify(sheetData) },
    ],
    temperature: isOpenAI ? 0.3 : 0.3,
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
    content: normalizeContent(rawContent),
    rawContent,
  };
}
