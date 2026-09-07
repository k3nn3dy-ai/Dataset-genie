import type { ModelInfo } from '../types'

// Representative OpenRouter catalogue slice. Prices are USD per 1M tokens (in / out).
const m = (id: string, name: string, ctx: number, pin: number, pout: number, json = true, tools = true): ModelInfo => ({
  id, name, context_length: ctx, prompt_price_per_m: pin, completion_price_per_m: pout, supports_json_schema: json, supports_tools: tools,
})

export const MODEL_CATALOGUE: ModelInfo[] = [
  m('anthropic/claude-sonnet-4', 'Claude Sonnet 4', 200_000, 3, 15),
  m('anthropic/claude-opus-4', 'Claude Opus 4', 200_000, 15, 75),
  m('anthropic/claude-3.5-haiku', 'Claude 3.5 Haiku', 200_000, 0.8, 4),
  m('openai/gpt-4o', 'GPT-4o', 128_000, 2.5, 10),
  m('openai/gpt-4o-mini', 'GPT-4o mini', 128_000, 0.15, 0.6),
  m('openai/gpt-4.1', 'GPT-4.1', 1_047_576, 2, 8),
  m('openai/gpt-4.1-mini', 'GPT-4.1 mini', 1_047_576, 0.4, 1.6),
  m('openai/gpt-4.1-nano', 'GPT-4.1 nano', 1_047_576, 0.1, 0.4),
  m('openai/o4-mini', 'o4-mini', 200_000, 1.1, 4.4),
  m('openai/o3', 'o3', 200_000, 2, 8),
  m('google/gemini-2.5-pro', 'Gemini 2.5 Pro', 1_048_576, 1.25, 10),
  m('google/gemini-2.5-flash', 'Gemini 2.5 Flash', 1_048_576, 0.3, 2.5),
  m('google/gemini-2.5-flash-lite', 'Gemini 2.5 Flash Lite', 1_048_576, 0.1, 0.4),
  m('google/gemma-3-27b-it', 'Gemma 3 27B', 131_072, 0.1, 0.2, false, false),
  m('meta-llama/llama-3.1-8b-instruct', 'Llama 3.1 8B Instruct', 131_072, 0.02, 0.05, false, true),
  m('meta-llama/llama-3.1-70b-instruct', 'Llama 3.1 70B Instruct', 131_072, 0.4, 0.4, false, true),
  m('meta-llama/llama-3.3-70b-instruct', 'Llama 3.3 70B Instruct', 131_072, 0.12, 0.3, false, true),
  m('meta-llama/llama-4-maverick', 'Llama 4 Maverick', 1_048_576, 0.15, 0.6, false, true),
  m('meta-llama/llama-4-scout', 'Llama 4 Scout', 327_680, 0.08, 0.3, false, true),
  m('mistralai/mistral-large-2411', 'Mistral Large 2411', 131_072, 2, 6),
  m('mistralai/mistral-small-3.2-24b-instruct', 'Mistral Small 3.2 24B', 131_072, 0.05, 0.1),
  m('mistralai/codestral-2501', 'Codestral 2501', 262_144, 0.3, 0.9, false, true),
  m('deepseek/deepseek-chat-v3-0324', 'DeepSeek V3 0324', 163_840, 0.27, 1.1, true, true),
  m('deepseek/deepseek-r1-0528', 'DeepSeek R1 0528', 163_840, 0.5, 2.15, false, false),
  m('qwen/qwen3-235b-a22b', 'Qwen3 235B A22B', 40_960, 0.13, 0.6, true, true),
  m('qwen/qwen3-32b', 'Qwen3 32B', 40_960, 0.1, 0.3, true, true),
  m('qwen/qwen-2.5-72b-instruct', 'Qwen2.5 72B Instruct', 131_072, 0.12, 0.39, true, true),
  m('x-ai/grok-4', 'Grok 4', 256_000, 3, 15),
  m('x-ai/grok-3-mini', 'Grok 3 Mini', 131_072, 0.3, 0.5),
  m('cohere/command-a', 'Command A', 256_000, 2.5, 10, false, true),
  m('nousresearch/hermes-3-llama-3.1-405b', 'Hermes 3 405B', 131_072, 0.7, 0.8, false, true),
  m('microsoft/phi-4', 'Phi 4', 16_384, 0.07, 0.14, false, false),
]

export function searchModels(q: string): ModelInfo[] {
  const needle = q.trim().toLowerCase()
  if (!needle) return MODEL_CATALOGUE
  return MODEL_CATALOGUE.filter((x) => x.id.toLowerCase().includes(needle) || x.name.toLowerCase().includes(needle))
}
