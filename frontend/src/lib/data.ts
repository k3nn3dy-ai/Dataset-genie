// Data access layer. One interface, two implementations (real API / mock). Screens only use `data`.
import { ApiError, subscribeRun } from './api'
import type { Estimate, ExportFormat, FilterConfig, HFPushConfig, Message, ModelInfo, Pair, Paged, Project, ProjectConfig, ProjectSummary, Run, RunEvent, TemplateName, TopicNode } from './types'
import type { ExportRecord, FilterSummary, HFStatus, JudgeSummary, Preset, PromptItem, RawCall, RefusalCell, ReviewStats, RowItem, SecretsStatus, SettingsData } from './viewtypes'
import { realApi } from './real'
import { useStore } from '../app/store'

export interface RowQuery { status?: string; leaf?: string; q?: string; min_score?: number; flags?: string[]; page?: number; page_size?: number }
/** Mirrors backend `export.ExportRequest`. */
export interface ExportRequest { formats: ExportFormat[]; eval_split: number; stratify_by: 'leaf' | 'topic' | 'difficulty' | 'none'; validate_template: TemplateName | null; include_judge_scores: boolean; gate_on_score: boolean; gate_threshold: number; seed: number; push: HFPushConfig | null }
export type RowAction = 'accept' | 'flag' | 'unflag' | 'restore'

export interface DataApi {
  listProjects(): Promise<Project[]>
  getProject(id: string): Promise<Project>
  getSummary(id: string): Promise<ProjectSummary>
  createProject(body: { preset: string; name: string; brief: string; data_types: string[] }): Promise<Project>
  patchProject(id: string, patch: Partial<Pick<Project, 'name' | 'domain_brief'>> & { config?: Partial<ProjectConfig> }): Promise<Project>
  deleteProject(id: string): Promise<void>
  getTaxonomy(id: string): Promise<TopicNode[]>
  putTaxonomy(id: string, tree: TopicNode[]): Promise<TopicNode[]>
  getPrompts(id: string, q: { leaf?: string; q?: string; page?: number }): Promise<Paged<PromptItem>>
  resamplePrompts(id: string, leaf: string): Promise<void>
  getRows(id: string, q: RowQuery): Promise<Paged<RowItem>>
  getRow(id: string, rid: string): Promise<RowItem>
  patchRow(id: string, rid: string, patch: { messages?: Message[]; action?: RowAction }): Promise<RowItem>
  bulkRows(id: string, ids: string[], action: RowAction): Promise<void>
  getPairs(id: string): Promise<Paged<Pair>>
  getRefusals(id: string): Promise<Paged<RowItem>>
  getRefusalMatrix(id: string): Promise<RefusalCell[]>
  getJudgeSummary(id: string): Promise<JudgeSummary>
  getFilterSummary(id: string): Promise<FilterSummary>
  runFilter(id: string, cfg: FilterConfig): Promise<FilterSummary>
  restoreRows(id: string, ids: string[]): Promise<void>
  estimate(id: string, stage: number, params: Record<string, unknown>): Promise<Estimate>
  runStage(id: string, stage: number, params: Record<string, unknown>): Promise<{ run_id: string }>
  getRun(runId: string): Promise<Run>
  cancelRun(runId: string): Promise<void>
  resumeRun(runId: string, force?: boolean): Promise<void>
  getRunLog(runId: string): Promise<Paged<RawCall>>
  subscribeRun(runId: string, onEvent: (ev: RunEvent) => void): () => void
  exportBundle(id: string, body: ExportRequest): Promise<ExportRecord>
  listExports(id: string): Promise<ExportRecord[]>
  hfStatus(id: string): Promise<HFStatus>
  reviewStats(id: string): Promise<ReviewStats>
  models(q: string): Promise<ModelInfo[]>
  refreshModels(): Promise<ModelInfo[]>
  getSettings(): Promise<SettingsData>
  putSettings(s: SettingsData): Promise<SettingsData>
  secretsStatus(): Promise<SecretsStatus>
  setSecret(name: 'openrouter' | 'huggingface', value: string): Promise<void>
  deleteSecret(name: 'openrouter' | 'huggingface'): Promise<void>
  presets(): Promise<Preset[]>
}

/**
 * Fall back to mock when the backend is absent (network error / proxy 502-504) or a route is still a 501 stub.
 * A 502-504 carrying the backend's own `{detail}` body is an application error (e.g. "bundle built but push
 * failed") — the backend is up, so surface it instead of hiding it behind mock data.
 */
function shouldFallback(e: unknown): boolean {
  if (e instanceof ApiError) {
    if (e.status === 501) return true
    if (e.status >= 502 && e.status <= 504) return !e.fromBackend
    return false
  }
  return e instanceof TypeError
}

type Fn = (...a: unknown[]) => unknown
let mockModule: Promise<DataApi> | null = null
const loadMock = (): Promise<DataApi> => (mockModule ??= import('./mock').then((m) => m.mockApi))

function wrap<K extends keyof DataApi>(key: K): DataApi[K] {
  const fn = async (...args: unknown[]) => {
    if (useStore.getState().mock) return ((await loadMock())[key] as Fn)(...args)
    try {
      return await (realApi[key] as Fn)(...args)
    } catch (e) {
      if (!shouldFallback(e)) throw e
      useStore.getState().setMock(true, e instanceof ApiError ? `API ${e.status}` : 'backend unreachable')
      return ((await loadMock())[key] as Fn)(...args)
    }
  }
  return fn as unknown as DataApi[K]
}

const keys = Object.keys(realApi) as (keyof DataApi)[]
const built = {} as Record<keyof DataApi, unknown>
for (const k of keys) if (k !== 'subscribeRun') built[k] = wrap(k)
built.subscribeRun = (runId: string, onEvent: (ev: RunEvent) => void): (() => void) => {
  if (useStore.getState().mock) {
    let unsub: (() => void) | null = null
    let closed = false
    void loadMock().then((m) => { if (!closed) unsub = m.subscribeRun(runId, onEvent) })
    return () => { closed = true; unsub?.() }
  }
  return subscribeRun(runId, onEvent, () => useStore.getState().setSseError(true))
}
export const data = built as unknown as DataApi
