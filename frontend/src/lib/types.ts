// Mirror of backend/genie/schemas.py. Keep in sync; the lead owns both files.
export type Role = 'system' | 'user' | 'assistant' | 'tool'
export type DataType = 'sft' | 'dpo' | 'tools' | 'grpo'
export type Difficulty = 'easy' | 'medium' | 'hard'
export type RowStatus = 'draft' | 'refusal' | 'filtered' | 'accepted' | 'edited' | 'flagged'
export type StageNumber = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8
export type RunStatus = 'queued' | 'running' | 'paused' | 'done' | 'failed' | 'cancelled' | 'budget_stop'

export interface ToolCall { id: string; type: 'function'; function: { name: string; arguments: string } }
export interface Message { role: Role; content: string | null; tool_calls?: ToolCall[] | null; tool_call_id?: string | null; name?: string | null }
export interface JudgeResult { score: number; criteria: Record<string, number>; rationale: string; verdict?: 'chosen' | 'rejected' | 'tie' | null }
export interface RowMetadata {
  id: string; leaf_id: string; leaf_path: string[]; difficulty: Difficulty; task_type: string
  persona?: string | null; style?: string | null; adversarial: boolean; models: Record<string, string>
  judge?: JudgeResult | null; flags: string[]; answer?: string | null; strategy?: string | null; flaw?: string | null
}
export interface Row { messages: Message[]; tools?: Record<string, unknown>[] | null; metadata: RowMetadata }
export interface Pair { prompt: Message[]; chosen: Message[]; rejected: Message[]; metadata: RowMetadata }

export interface ModelSlot { slug: string; provider_order: string[]; allow_fallbacks: boolean; temperature: number; max_tokens: number; weight: number }
export interface Persona { name: string; style: string; weight: number }
export interface RubricCriterion { name: string; weight: number; description: string }
export interface FlawWeight { name: string; weight: number; instruction: string }

export interface TaxonomyConfig { model: ModelSlot; depth: number; topics: number; subtopics_per_topic: number; leaves_per_topic: number; difficulty_tiers: boolean; negative_branches: boolean; rows_per_leaf: number; task_types: string[] }
export interface PromptsConfig { model: ModelSlot; personas: Persona[]; style_mix: Record<string, number>; temperature: number; noise_level: number; adversarial_pct: number; near_dup_threshold: number; embedding_model: string }
export interface ResponsesConfig { ensemble: ModelSlot[]; selection: 'round-robin' | 'weighted'; temperature: number; max_tokens: number; system_prompt: string; system_prompt_policy: 'always' | 'never' | 'random'; system_prompt_random_pct: number; multi_turn: boolean; simulated_user_model: ModelSlot; turns_min: number; turns_max: number; user_mood: 'cooperative' | 'confused' | 'hostile'; reasoning_tags: boolean }
export interface PreferencesConfig { strategy: 'corruptor' | 'weaker' | 'hightemp'; weaker_model: ModelSlot; hightemp_temperature: number; flaws: FlawWeight[] }
export interface JudgeConfig { model: ModelSlot; rubric: RubricCriterion[]; low_score_threshold: number; drop_ties_from_dpo: boolean }
export interface FilterConfig { exact_dup: boolean; near_dup: boolean; near_dup_threshold: number; refusal: boolean; pii: boolean; length: boolean; min_chars: number; max_chars: number; language: boolean; expected_language: string; embedding_model: string }
export interface HFPushConfig { repo_id: string; private: boolean; license: string; version_tag: string }
export type ExportFormat = 'sft' | 'alpaca' | 'dpo' | 'tools' | 'grpo'
export type TemplateName = 'llama-3.1' | 'chatml' | 'gemma'
export interface ExportConfig { formats: ExportFormat[]; eval_split: number; stratify_by: 'leaf' | 'topic' | 'difficulty' | 'none'; validate_template: TemplateName | null; include_judge_scores: boolean; gate_on_score: boolean; gate_threshold: number; seed: number; hf: HFPushConfig }
export interface ProjectConfig {
  data_types: DataType[]; budget_cap_usd: number; stop_at_pct: number; concurrency: number
  prefer_prompt_caching: boolean; allow_fallback_providers: boolean
  taxonomy: TaxonomyConfig; prompts: PromptsConfig; responses: ResponsesConfig; preferences: PreferencesConfig
  judge: JudgeConfig; filters: FilterConfig; export: ExportConfig; tools_schemas: Record<string, unknown>[]
}

export interface Project { id: string; slug: string; name: string; domain_brief: string; preset?: string | null; data_types: DataType[]; config: ProjectConfig; budget_cap_usd: number; stop_at_pct: number; spend_usd: number; created_at: number; updated_at: number }
export interface StageStatus { stage: StageNumber; status: 'todo' | 'running' | 'paused' | 'done' | 'failed'; run_id?: string | null; run_status?: RunStatus | null; latest_run_status?: RunStatus | null; count: number }
export interface ProjectSummary { project: Project; stages: StageStatus[]; rows: number; pairs: number; refusals: number; filtered: number; accepted: number; spend_usd: number; cap_usd: number; target_rows: number }
export interface Run { id: string; project_id: string; stage: StageNumber; status: RunStatus; model_slug?: string | null; params: Record<string, unknown>; done: number; total: number; errors: number; refusals: number; spend_usd: number; est_usd: number; error_message?: string | null; started_at?: number | null; finished_at?: number | null }
export interface Estimate { est_usd: number; calls: number; est_tokens_in: number; est_tokens_out: number; over_cap: boolean }

export interface ProgressEvent { type: 'progress'; done: number; total: number; rows_per_min: number; refusals: number; errors: number; spend_usd: number; cap_usd: number }
export interface WorkerEvent { type: 'worker'; worker_id: number; status: 'idle' | 'calling' | 'done' | 'error'; target_id?: string | null; model?: string | null }
export interface LogEvent { type: 'log'; level: 'debug' | 'info' | 'warn' | 'error'; ts: number; msg: string }
export interface ItemEvent { type: 'item'; target_id: string; status: 'done' | 'error' | 'refusal' | 'skipped' }
export interface DoneEvent { type: 'done'; status: RunStatus }
export type RunEvent = ProgressEvent | WorkerEvent | LogEvent | ItemEvent | DoneEvent

export interface ModelInfo { id: string; name: string; context_length: number; prompt_price_per_m: number; completion_price_per_m: number; supports_json_schema: boolean; supports_tools: boolean }
export interface TopicNode { id: string; parent_id: string | null; depth: number; label: string; slug: string; difficulty?: Difficulty | null; task_type?: string | null; is_negative: boolean; is_leaf: boolean; rows_per_leaf?: number | null; order: number; children?: TopicNode[] }
export interface Paged<T> { items: T[]; total: number; page: number; page_size: number }

export const STAGES: { n: StageNumber; key: string; title: string; kicker: string; kana: string }[] = [
  { n: 1, key: 'taxonomy', title: 'Taxonomy', kicker: 'MAP THE DOMAIN', kana: '分類' },
  { n: 2, key: 'prompts', title: 'Prompts', kicker: 'VOICES OF THE USER', kana: 'プロンプト' },
  { n: 3, key: 'responses', title: 'Responses', kicker: 'THE TEACHER ANSWERS', kana: '応答' },
  { n: 4, key: 'rejected', title: 'Rejected', kicker: 'MANUFACTURE THE FLAW', kana: '却下' },
  { n: 5, key: 'judge', title: 'Judge', kicker: 'A SECOND OPINION', kana: '審査' },
  { n: 6, key: 'filter', title: 'Filter', kicker: 'CLEAN THE SIGNAL', kana: '濾過' },
  { n: 7, key: 'review', title: 'Review', kicker: 'HUMAN IN THE LOOP', kana: '検査' },
  { n: 8, key: 'export', title: 'Export', kicker: 'SHIP THE DATASET', kana: '出力' },
]
export const KANA = { projects: 'プロジェクト', settings: '設定', wordmark: 'データセット・ジーニー' }
