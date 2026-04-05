import { AIServiceProvider } from './base.js';

export class KimiProvider extends AIServiceProvider {
  constructor() {
    super({
      name: 'kimi',
      endpoint: '/api/ai/kimi',
      authType: 'proxy',
      model: 'kimi-k2-5',
      temperature: 0.3,
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

    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`Kimi request failed (${res.status}): ${errText}`);
    }

    const json = await res.json();
    return {
      provider: 'kimi',
      content: json?.choices?.[0]?.message?.content ?? '',
      usage: json?.usage ?? null,
      model: json?.model ?? this.config.model,
    };
  }
}
