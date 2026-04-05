export async function analyzeWithFallback(sheetData, task, preferredProvider = 'kimi') {
  const aiPayload = {
    model: preferredProvider === 'openai' ? 'gpt-4.1-mini' : 'kimi-k2-5',
    messages: [
      { role: 'system', content: `You are analyzing spreadsheet data. Task: ${task}. Return JSON.` },
      { role: 'user', content: JSON.stringify(sheetData) },
    ],
    temperature: 0.3,
    response_format: { type: 'json_object' },
  };

  const providerOrder = preferredProvider === 'openai' ? ['openai', 'kimi'] : ['kimi', 'openai'];
  const res = await fetch('/api/ai/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sheetData, aiPayload, providerOrder }),
  });
  const data = await res.json();
  if (!res.ok) {
    const details = JSON.stringify(data.attempts || data, null, 2);
    throw new Error(`Analysis failed: ${details}`);
  }

  return {
    provider: data.provider,
    model: data.model,
    usage: data.usage,
    content: data?.choices?.[0]?.message?.content ?? '',
  };
}
