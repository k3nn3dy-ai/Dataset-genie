// Data access layer. One interface, two implementations (real API / mock). Screens only use `data`.
import { ApiError, api, subscribeRun } from './api'
import type { Estimate, FilterConfig, Message, ModelInfo, Pair, Paged, Project, ProjectConfig, ProjectSummary, Run, RunEvent, TopicNode } from './types'
import type { ExportRecord, FilterSummary, JudgeSummary, Preset, PromptItem, RawCall, RefusalCell, RowItem, SecretsStatus, SettingsData } from './viewtypes'
import { useStore } from '../app/store'

export interface RowQuery { status?: string; leaf?: string; q?: string; min_score?: number; flags?: string[]; page?: number; page_size?: number }
export interface ExportRequest { formats: ExportRecord['formats']; split: number; eval_split: number; stratify_by: string; validate_template: string | null; include_scores: boolean; push?: { repo: string; private: boolean; license: string; tag: string } | null }
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
  resumeRun(runId: string): Promise<void>
  getRunLog(runId: string): Promise<Paged<RawCall>>
  subscribeRun(runId: string, onEvent: (ev: RunEvent) => void): () => void
  exportBundle(id: string, body: ExportRequest): Promise<ExportRecord>
  listExports(id: string): Promise<ExportRecord[]>
  models(q: string): Promise<ModelInfo[]>
  refreshModels(): Promise<ModelInfo[]>
  getSettings(): Promise<SettingsData>
  putSettings(s: SettingsData): Promise<SettingsData>
  secretsStatus(): Promise<SecretsStatus>
  setSecret(name: 'openrouter' | 'huggingface', value: string): Promise<void>
  deleteSecret(name: 'openrouter' | 'huggingface'): Promise<void>
  presets(): Promise<Preset[]>
}

const qs = (o: Record<string, unknown>): string => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== '' && v !== null) p.set(k, Array.isArray(v) ? v.join(',') : String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

const realApi: DataApi = {
  listProjects: () => api.get('/projects'),
  getProject: (id) => api.get(`/projects/${id}`),
  getSummary: (id) => api.get(`/projects/${id}/summary`),
  createProject: (b) => api.post('/projects/from-preset', b),
  patchProject: (id, patch) => api.patch(`/projects/${id}`, patch),
  deleteProject: (id) => api.del(`/projects/${id}`),
  getTaxonomy: (id) => api.get(`/projects/${id}/taxonomy`),
  putTaxonomy: (id, tree) => api.put(`/projects/${id}/taxonomy`, tree),
  getPrompts: (id, q) => api.get(`/projects/${id}/prompts${qs(q)}`),
  resamplePrompts: (id, leaf) => api.post(`/projects/${id}/prompts/resample`, { leaf_id: leaf }),
  getRows: (id, q) => api.get(`/projects/${id}/rows${qs({ ...q })}`),
  getRow: (id, rid) => api.get(`/projects/${id}/rows/${rid}`),
  patchRow: (id, rid, patch) => api.patch(`/projects/${id}/rows/${rid}`, patch),
  bulkRows: (id, ids, action) => api.post(`/projects/${id}/rows/bulk`, { ids, action }),
  getPairs: (id) => api.get(`/projects/${id}/pairs`),
  getRefusals: (id) => api.get(`/projects/${id}/refusals`),
  getRefusalMatrix: async (id) => {
    const rows = await api.get<Paged<RowItem>>(`/projects/${id}/rows?page_size=2000`)
    const cells = new Map<string, RefusalCell>()
    for (const r of rows.items) {
      const key = `${r.metadata.models.responses}|${r.metadata.leaf_path[0]}`
      const c = cells.get(key) ?? { model: r.metadata.models.responses, topic: r.metadata.leaf_path[0], refusals: 0, total: 0 }
      c.total++; if (r.status === 'refusal') c.refusals++; cells.set(key, c)
    }
    return [...cells.values()]
  },
  getJudgeSummary: (id) => api.get(`/projects/${id}/judge/summary`),
  getFilterSummary: (id) => api.get(`/projects/${id}/filter/summary`),
  runFilter: (id, cfg) => api.post(`/projects/${id}/filter/run`, cfg),
  restoreRows: (id, ids) => api.post(`/projects/${id}/filter/restore`, { ids }),
  estimate: (id, stage, params) => api.post(`/projects/${id}/stages/${stage}/estimate`, { params }),
  runStage: (id, stage, params) => api.post(`/projects/${id}/stages/${stage}/run`, { params }),
  getRun: (runId) => api.get(`/runs/${runId}`),
  cancelRun: (runId) => api.post(`/runs/${runId}/cancel`),
  resumeRun: (runId) => api.post(`/runs/${runId}/resume`),
  getRunLog: (runId) => api.get(`/runs/${runId}/log`),
  subscribeRun: (runId, onEvent) => subscribeRun(runId, onEvent),
  exportBundle: (id, body) => api.post(`/projects/${id}/export`, body),
  listExports: (id) => api.get(`/projects/${id}/exports`),
  models: (q) => api.get(`/models${qs({ q })}`),
  refreshModels: () => api.get('/models/refresh'),
  getSettings: () => api.get('/settings'),
  putSettings: (s) => api.put('/settings', s),
  secretsStatus: () => api.get('/settings/secrets/status'),
  setSecret: (name, value) => api.put('/settings/secrets', { name, value }),
  deleteSecret: (name) => api.del(`/settings/secrets/${name}`),
  presets: () => api.get('/presets'),
}

/** Fall back to mock when the backend is absent (network error / proxy 502-504) or a route is still a 501 stub. */
function shouldFallback(e: unknown): boolean {
  if (e instanceof ApiError) return e.status === 501 || e.status === 502 || e.status === 503 || e.status === 504
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
