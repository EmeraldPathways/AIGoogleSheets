import { AIServiceProvider } from './base.js';

export class OpenAIProvider extends AIServiceProvider {
  constructor() {
    super({
      name: 'openai',
      endpoint: '/api/ai/openai',
      authType: 'proxy',
      model: 'gpt-4.1-mini',
      temperature: 0.2,
    });
  }

  async analyze(sheetData, taskType) {
    const prompt = this.formatPrompt(sheetData, taskType);
    const res = await fetch(this.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: this.config.model,
        messages: [
          { role: 'system', content: prompt.system },
          { role: 'user', content: JSON.stringify(prompt.data) },
        ],
        temperature: this.config.temperature,
        response_format: { type: 'json_object' },
      }),
    });

    const json = await res.json();
    if (!res.ok) throw new Error(json.error || 'OpenAI request failed');
    return {
      provider: 'openai',
      content: json?.choices?.[0]?.message?.content ?? '',
      usage: json?.usage ?? null,
      model: json?.model ?? this.config.model,
    };
  }
}
