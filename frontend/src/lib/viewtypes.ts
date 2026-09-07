// UI-side types that are not part of the canonical schema mirror in types.ts.
import type { Difficulty, ExportFormat, ModelSlot, Row, RowStatus, RunStatus } from './types'

export interface PromptItem {
  id: string
  leaf_id: string
  leaf_path: string[]
  text: string
  persona: string
  style: string
  difficulty: Difficulty
  adversarial: boolean
}

export type RowItem = Row & { status: RowStatus; filter_reason?: string | null }

export interface JudgeSummary {
  judged: number
  mean: number
  histogram: number[] // 10 bins over 0–5
  ties: number
  below_threshold: number
  family_warning: string | null
}

export interface FilterRuleSummary {
  name: 'exact_dup' | 'near_dup' | 'refusal' | 'pii' | 'length' | 'language'
  label: string
  enabled: boolean
  removed: number
}

export interface FilterSummary {
  rules: FilterRuleSummary[]
  removed: RowItem[]
  refusals: RowItem[]
  kept: number
}

export interface RefusalCell { model: string; topic: string; refusals: number; total: number }

export interface ExportRecord {
  id: string
  created_at: number
  formats: ExportFormat[]
  path: string
  rows_train: number
  rows_eval: number
  hf_url?: string | null
  status: 'ok' | 'failed'
}

export interface Preset { id: string; name: string; description: string; data_types: string[] }

export interface SettingsData {
  default_models: Record<'taxonomy' | 'prompts' | 'responses' | 'judge' | 'simulated_user', ModelSlot>
  budget_cap_usd: number
  stop_at_pct: number
  concurrency: number
  prefer_prompt_caching: boolean
  allow_fallback_providers: boolean
  pinned_provider: string
}

export interface SecretsStatus { openrouter: boolean; huggingface: boolean; hf_user?: string | null }

export interface RawCall {
  id: string
  ts: number
  model: string
  target_id: string
  status: 'ok' | 'error' | 'refusal'
  tokens_in: number
  tokens_out: number
  cost_usd: number
  latency_ms: number
  error?: string | null
}

export interface RunSnapshot {
  runId: string
  status: RunStatus
  done: number
  total: number
  rows_per_min: number
  refusals: number
  errors: number
  spend_usd: number
  cap_usd: number
  workers: Record<number, { status: 'idle' | 'calling' | 'done' | 'error'; target_id?: string | null; model?: string | null }>
  log: { level: 'debug' | 'info' | 'warn' | 'error'; ts: number; msg: string }[]
}
