// Real API implementation of DataApi. Adapts server shapes (see backend/genie/api/*.py) to the view types.
import { ApiError, api, subscribeRun } from './api'
import type { Message, ModelSlot, Pair, Paged, Project, TopicNode } from './types'
import type { ExportRecord, FilterRuleSummary, FilterSummary, HFStatus, JudgeSummary, Preset, PromptItem, RawCall, RefusalCell, ReviewStats, RowItem, SettingsData } from './viewtypes'
import type { DataApi, RowAction, RowQuery } from './data'
import { useStore } from '../app/store'

const qs = (o: Record<string, unknown>): string => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== '' && v !== null) p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

/** Walk a paged endpoint (server caps page_size at 1000) until exhausted. */
async function walk<T>(fetchPage: (page: number, size: number) => Promise<Paged<T>>, max = 5000): Promise<Paged<T>> {
  const size = 500
  const items: T[] = []
  let total = 0
  for (let page = 1; items.length < max; page++) {
    const r = await fetchPage(page, size)
    items.push(...r.items)
    total = r.total
    if (r.items.length < size || items.length >= total) break
  }
  return { items, total, page: 1, page_size: items.length }
}

// ---------------------------------------------------------------- server shapes (only the fields we read)
interface SrvPrompt { id: string; leaf_id: string; leaf_label: string | null; text: string; persona: string | null; style: string | null; adversarial: boolean }
interface SrvPair { id: string; row_id: string; rejected_messages: Message[]; strategy: string; flaw: string | null; status: string; judge: Pair['metadata']['judge']; chosen_row: RowItem | null }
interface SrvJudge { histogram: number[]; mean: number; low_count: number; threshold: number; ties: number; judged_rows: number; same_family_warning: boolean; teacher_families: string[]; judge_family: string; judge_model: string }
interface SrvFilter { rules: Record<string, { enabled: boolean; removed: number }>; removed_total: number; refusals: number; candidates: number; removed: Paged<RowItem> }
interface SrvRawCall { id: string; target_id: string | null; model_slug: string | null; usage: { prompt_tokens?: number; completion_tokens?: number } | null; cost_usd: number | null; latency_ms: number | null; error: string | null; created_at: number }
interface SrvExport { id: string; path: string; formats: ExportRecord['formats']; counts: Record<string, { train?: number; eval?: number; total?: number }>; hf_repo: string | null; hf_url: string | null; created_at: number }
interface SrvExportResp { export_id: string; path: string; formats: ExportRecord['formats']; counts: SrvExport['counts']; files: string[]; warnings: string[]; gated_out: number; hf_url: string | null }
interface SrvSettings { default_models: Record<string, string>; budget_cap_usd: number; stop_at_pct: number; concurrency: number; prefer_prompt_caching: boolean; allow_fallback_providers: boolean; provider_order: string[]; catalogue_ttl_hours?: number }
interface SrvPreset { key: string; name: string; description: string; data_types: string[] }

const RULE_LABEL: Record<FilterRuleSummary['name'], string> = { exact_dup: 'Exact duplicate', near_dup: 'Near duplicate', refusal: 'Refusal → bucket', pii: 'PII', length: 'Length bounds', language: 'Language' }
const slot = (slug: string, temperature = 0.7): ModelSlot => ({ slug, provider_order: [], allow_fallbacks: true, temperature, max_tokens: 2048, weight: 1 })

const toPrompt = (p: SrvPrompt): PromptItem => ({ id: p.id, leaf_id: p.leaf_id, leaf_path: [p.leaf_label ?? p.leaf_id], text: p.text, persona: p.persona ?? '', style: p.style ?? '', difficulty: 'medium', adversarial: p.adversarial })
const toPair = (p: SrvPair): Pair | null => {
  const row = p.chosen_row
  if (!row) return null
  const last = row.messages.at(-1)
  const rej = p.rejected_messages.at(-1)
  return {
    prompt: row.messages.slice(0, -1), chosen: last ? [last] : [], rejected: rej ? [rej] : [],
    metadata: { ...row.metadata, id: row.metadata.id, strategy: p.strategy, flaw: p.flaw, judge: p.judge ? { ...p.judge, verdict: p.status === 'tie' ? 'tie' : p.judge.verdict } : null },
  }
}
const toCall = (c: SrvRawCall): RawCall => ({ id: c.id, ts: c.created_at, model: c.model_slug ?? '', target_id: c.target_id ?? '', status: c.error ? 'error' : 'ok', tokens_in: c.usage?.prompt_tokens ?? 0, tokens_out: c.usage?.completion_tokens ?? 0, cost_usd: c.cost_usd ?? 0, latency_ms: c.latency_ms ?? 0, error: c.error })
const toExport = (x: SrvExport): ExportRecord => {
  const f0 = x.counts[x.formats[0]] ?? {}
  return { id: x.id, created_at: x.created_at, formats: x.formats, path: x.path, rows_train: f0.train ?? 0, rows_eval: f0.eval ?? 0, hf_url: x.hf_url, status: 'ok', counts: x.counts }
}
const toSettings = (s: SrvSettings): SettingsData => ({
  default_models: { taxonomy: slot(s.default_models.taxonomy, 0.4), prompts: slot(s.default_models.prompts, 0.9), responses: slot(s.default_models.responses), judge: slot(s.default_models.judge, 0), simulated_user: slot(s.default_models.simulated_user, 0.9), weaker: slot(s.default_models.weaker ?? 'meta-llama/llama-3.1-8b-instruct') },
  embedding_model: s.default_models.embeddings ?? 'openai/text-embedding-3-small',
  budget_cap_usd: s.budget_cap_usd, stop_at_pct: s.stop_at_pct, concurrency: s.concurrency, prefer_prompt_caching: s.prefer_prompt_caching, allow_fallback_providers: s.allow_fallback_providers, pinned_provider: s.provider_order[0] ?? '',
})
const fromSettings = (s: SettingsData): SrvSettings => ({
  default_models: { ...Object.fromEntries(Object.entries(s.default_models).map(([k, v]) => [k, v.slug])), embeddings: s.embedding_model },
  budget_cap_usd: s.budget_cap_usd, stop_at_pct: s.stop_at_pct, concurrency: s.concurrency, prefer_prompt_caching: s.prefer_prompt_caching, allow_fallback_providers: s.allow_fallback_providers, provider_order: s.pinned_provider ? [s.pinned_provider] : [],
})

async function filterSummary(id: string, s: SrvFilter): Promise<FilterSummary> {
  const refusals = await walk<RowItem>((page, size) => api.get(`/projects/${id}/refusals${qs({ page, page_size: size })}`))
  const rules = (Object.keys(RULE_LABEL) as FilterRuleSummary['name'][]).map((name) => ({ name, label: RULE_LABEL[name], enabled: s.rules[name]?.enabled ?? false, removed: name === 'refusal' ? s.refusals : s.rules[name]?.removed ?? 0 }))
  return { rules, removed: s.removed.items, refusals: refusals.items, kept: Math.max(0, s.candidates - s.removed_total) }
}

const rowsPage = (id: string, q: RowQuery) => (page: number, size: number) =>
  api.get<Paged<RowItem>>(`/projects/${id}/rows${qs({ status: q.status === 'all' ? undefined : q.status, leaf_id: q.leaf, q: q.q, min_score: q.min_score, flag: q.flags?.[0], page, page_size: size })}`)

async function bulk(id: string, ids: string[], action: RowAction): Promise<void> { await api.post(`/projects/${id}/rows/bulk`, { ids, action }) }

export const realApi: DataApi = {
  listProjects: () => api.get('/projects/'),
  getProject: (id) => api.get(`/projects/${id}`),
  getSummary: (id) => api.get(`/projects/${id}/summary`),
  createProject: async ({ preset, name, brief, data_types }) => {
    const p = await api.post<Project>('/projects/from-preset', { preset, name, brief })
    const same = p.data_types.length === data_types.length && p.data_types.every((d) => data_types.includes(d))
    return same ? p : api.patch<Project>(`/projects/${p.id}`, { data_types })
  },
  patchProject: (id, patch) => api.patch(`/projects/${id}`, patch),
  deleteProject: (id) => api.del(`/projects/${id}`),
  getTaxonomy: async (id) => (await api.get<{ tree: TopicNode[] }>(`/projects/${id}/taxonomy`)).tree,
  putTaxonomy: async (id, tree) => (await api.put<{ tree: TopicNode[] }>(`/projects/${id}/taxonomy`, { tree })).tree,
  getPrompts: async (id, q) => {
    const r = await walk<SrvPrompt>((page, size) => api.get(`/projects/${id}/prompts${qs({ leaf_id: q.leaf, q: q.q, page, page_size: size })}`))
    return { ...r, items: r.items.map(toPrompt) }
  },
  resamplePrompts: (id, leaf) => api.post(`/projects/${id}/prompts/resample`, { leaf_id: leaf }),
  getRows: async (id, q) => {
    const r = (q.page_size ?? 50) > 1000 ? await walk(rowsPage(id, q)) : await rowsPage(id, q)(q.page ?? 1, q.page_size ?? 50)
    const rest = (q.flags ?? []).slice(1)
    return rest.length ? { ...r, items: r.items.filter((x) => rest.every((f) => x.metadata.flags.includes(f))) } : r
  },
  getRow: (id, rid) => api.get(`/projects/${id}/rows/${rid}`),
  patchRow: async (id, rid, patch) => {
    if (patch.messages) await api.patch(`/projects/${id}/rows/${rid}`, { messages: patch.messages })
    if (patch.action) await bulk(id, [rid], patch.action)
    return api.get(`/projects/${id}/rows/${rid}`)
  },
  bulkRows: bulk,
  getPairs: async (id) => {
    const r = await walk<SrvPair>((page, size) => api.get(`/projects/${id}/pairs${qs({ page, page_size: size })}`))
    return { ...r, items: r.items.map(toPair).filter((p): p is Pair => p !== null) }
  },
  getRefusals: (id) => walk<RowItem>((page, size) => api.get(`/projects/${id}/refusals${qs({ page, page_size: size })}`)),
  getRefusalMatrix: async (id) => {
    const rows = await walk(rowsPage(id, {}))
    const cells = new Map<string, RefusalCell>()
    for (const r of rows.items) {
      const key = `${r.metadata.models.responses}|${r.metadata.leaf_path[0]}`
      const c = cells.get(key) ?? { model: r.metadata.models.responses, topic: r.metadata.leaf_path[0], refusals: 0, total: 0 }
      c.total++; if (r.status === 'refusal') c.refusals++; cells.set(key, c)
    }
    return [...cells.values()]
  },
  getJudgeSummary: async (id) => {
    const s = await api.get<SrvJudge>(`/projects/${id}/judge/summary`)
    const out: JudgeSummary = { judged: s.judged_rows, mean: s.mean, histogram: s.histogram.length === 10 ? s.histogram : new Array<number>(10).fill(0), ties: s.ties, below_threshold: s.low_count, family_warning: s.same_family_warning ? `Judge ${s.judge_model} (${s.judge_family}) shares a family with a teacher (${s.teacher_families.join(', ')}); scores may be self-preferential.` : null }
    return out
  },
  getFilterSummary: async (id) => filterSummary(id, await api.get<SrvFilter>(`/projects/${id}/filter/summary?page_size=500`)),
  runFilter: async (id, cfg) => {
    const r = await api.post<{ summary: SrvFilter }>(`/projects/${id}/filter/run`, { config: cfg })
    return filterSummary(id, r.summary)
  },
  restoreRows: (id, ids) => api.post(`/projects/${id}/filter/restore`, { ids }),
  estimate: (id, stage, params) => api.post(`/projects/${id}/stages/${stage}/estimate`, { params }),
  runStage: (id, stage, params) => api.post(`/projects/${id}/stages/${stage}/run`, { params }),
  getRun: (runId) => api.get(`/runs/${runId}`),
  cancelRun: (runId) => api.post(`/runs/${runId}/cancel`),
  resumeRun: (runId) => api.post(`/runs/${runId}/resume`),
  getRunLog: async (runId) => { const r = await api.get<Paged<SrvRawCall>>(`/runs/${runId}/log?page_size=200`); return { ...r, items: r.items.map(toCall).reverse() } },
  subscribeRun: (runId, onEvent) => subscribeRun(runId, onEvent),
  exportBundle: async (id, body) => {
    const r = await api.post<SrvExportResp>(`/projects/${id}/export`, body)
    const rec = toExport({ id: r.export_id, path: r.path, formats: r.formats, counts: r.counts, hf_repo: body.push?.repo_id ?? null, hf_url: r.hf_url, created_at: Date.now() / 1000 })
    return { ...rec, warnings: r.warnings, gated_out: r.gated_out }
  },
  listExports: async (id) => (await api.get<SrvExport[]>(`/projects/${id}/exports`)).map(toExport),
  hfStatus: (id) => api.get<HFStatus>(`/projects/${id}/hf/status`),
  reviewStats: (id) => api.get<ReviewStats>(`/projects/${id}/review/stats`),
  models: async (q) => {
    const res = await fetch(`/api/models/${qs({ q })}`)
    if (!res.ok) throw new ApiError(res.status, res.statusText)
    useStore.getState().setModelsWarning(res.headers.get('x-genie-warning'))
    return res.json()
  },
  refreshModels: () => api.get('/models/refresh'),
  getSettings: async () => toSettings(await api.get<SrvSettings>('/settings/')),
  putSettings: async (s) => toSettings(await api.put<SrvSettings>('/settings/', fromSettings(s))),
  secretsStatus: () => api.get('/settings/secrets/status'),
  setSecret: (name, value) => api.put('/settings/secrets', { name, value }),
  deleteSecret: (name) => api.del(`/settings/secrets/${name}`),
  presets: async () => (await api.get<SrvPreset[]>('/presets/')).map((p) => ({ id: p.key, name: p.name, description: p.description, data_types: p.data_types }) as Preset),
}
