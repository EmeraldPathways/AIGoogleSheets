import { KimiProvider } from './providers/kimi.js';
import { OpenAIProvider } from './providers/openai.js';

const providers = {
  kimi: new KimiProvider(),
  openai: new OpenAIProvider(),
};

export function getProvider(name) {
  return providers[name] || providers.kimi;
}
