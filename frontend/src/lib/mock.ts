// In-memory mock backend. Same surface as the real API (see data.ts); state is mutable for the session.
import type { Estimate, ModelInfo, Pair, Paged, Project, ProjectConfig, ProjectSummary, Run, StageNumber, StageStatus, TopicNode } from './types'
import type { ExportRecord, FilterSummary, HFStatus, JudgeSummary, Preset, PromptItem, RawCall, RefusalCell, ReviewStats, RowItem, SecretsStatus, SettingsData } from './viewtypes'
import { MODEL_CATALOGUE, searchModels } from './mock/models'
import { PRESETS, buildTaxonomy, defaultConfig, flattenLeaves, makeProject, slot } from './mock/project'
import { generate } from './mock/rows'
import { cancelMockRun, getMockRun, liveMockRun, resumeMockRun, seedPausedRun, startMockRun, subscribeMockRun } from './mock/run'
import { ApiError } from './api'
import { modelFamily } from './format'
import type { DataApi, RowQuery } from './data'

interface ProjState { project: Project; tree: TopicNode[]; prompts: PromptItem[]; rows: RowItem[]; pairs: Pair[]; stagesDone: number; exports: ExportRecord[]; restored: Set<string>; paused?: { stage: number; runId: string } | null }
const state = new Map<string, ProjState>()

function seedDemo(): void {
  const demoTree = buildTaxonomy()
  const demo = makeProject('p_linux', 'Linux incident triage', 'On-call assistant for Linux production incidents: disk, memory, systemd, network and access failures. Diagnosis first, then commands, then the next decision point. Refuse requests to disable security controls.', 'support-assistant', {}, 6.42)
  const gen = generate(demoTree, demo.slug)
  state.set(demo.id, { project: demo, tree: demoTree, ...gen, stagesDone: 6, exports: [
    { id: 'x1', created_at: Date.now() / 1000 - 5400, formats: ['sft'], path: '~/.dataset-genie/exports/linux-incident-triage/20260906-142210', rows_train: 92, rows_eval: 5, hf_url: null, status: 'ok' },
    { id: 'x2', created_at: Date.now() / 1000 - 1200, formats: ['sft', 'dpo'], path: '~/.dataset-genie/exports/linux-incident-triage/20260907-101833', rows_train: 96, rows_eval: 5, hf_url: 'https://huggingface.co/datasets/k3nn3dy/linux-incident-triage', status: 'ok' },
  ], restored: new Set() })
  const k8s = makeProject('p_k8s', 'Kubernetes runbooks', 'Cluster operators asking about pod scheduling, networking and rollout failures.', 'domain-expert', { data_types: ['sft'], budget_cap_usd: 25 }, 3.1)
  const k8sTree = buildTaxonomy().slice(0, 2)
  const k8sGen = generate(k8sTree, k8s.slug, 11)
  seedPausedRun('run_paused', 3, k8sGen.prompts.length, 17)
  state.set(k8s.id, { project: k8s, tree: k8sTree, prompts: k8sGen.prompts, rows: k8sGen.rows.slice(0, 17), pairs: [], stagesDone: 2, exports: [], restored: new Set(), paused: { stage: 3, runId: 'run_paused' } })
  const fresh = makeProject('p_grafana', 'Grafana alert explainer', 'Explain firing alerts in plain language and propose the first check.', 'domain-expert', { data_types: ['sft'] }, 0)
  state.set(fresh.id, { project: fresh, tree: [], prompts: [], rows: [], pairs: [], stagesDone: 0, exports: [], restored: new Set() })
}
seedDemo()

const delay = <T,>(v: T, ms = 120): Promise<T> => new Promise((r) => setTimeout(() => r(v), ms))
const need = (id: string): ProjState => { const s = state.get(id); if (!s) throw new Error(`project ${id} not found`); return s }
const paged = <T,>(items: T[], page = 1, size = 50): Paged<T> => ({ items: items.slice((page - 1) * size, page * size), total: items.length, page, page_size: size })

function summary(s: ProjState): ProjectSummary {
  const stages: StageStatus[] = ([1, 2, 3, 4, 5, 6, 7, 8] as StageNumber[]).map((n) => ({
    stage: n, status: (s.paused?.stage === n ? 'paused' : n <= s.stagesDone ? 'done' : n === s.stagesDone + 1 && s.stagesDone > 0 ? 'running' : 'todo') as StageStatus['status'],
    run_id: s.paused?.stage === n ? s.paused.runId : null, count: n === 1 ? flattenLeaves(s.tree).length : n === 2 ? s.prompts.length : n === 4 ? s.pairs.length : n === 8 ? s.exports.length : s.rows.length,
  }))
  const leaves = flattenLeaves(s.tree).length
  return {
    project: s.project, stages, rows: s.rows.length, pairs: s.pairs.length,
    refusals: s.rows.filter((r) => r.status === 'refusal').length, filtered: s.rows.filter((r) => r.status === 'filtered').length,
    accepted: s.rows.filter((r) => r.status === 'accepted' || r.status === 'edited').length,
    spend_usd: s.project.spend_usd, cap_usd: s.project.budget_cap_usd, target_rows: leaves * s.project.config.taxonomy.rows_per_leaf,
  }
}

let settings: SettingsData = {
  default_models: { taxonomy: slot('anthropic/claude-sonnet-4', { temperature: 0.4 }), prompts: slot('openai/gpt-4o-mini', { temperature: 0.9 }), responses: slot('anthropic/claude-sonnet-4'), judge: slot('openai/gpt-4o', { temperature: 0 }), simulated_user: slot('openai/gpt-4o-mini', { temperature: 0.9 }), weaker: slot('meta-llama/llama-3.1-8b-instruct') },
  embedding_model: 'openai/text-embedding-3-small',
  budget_cap_usd: 15, stop_at_pct: 90, concurrency: 8, prefer_prompt_caching: true, allow_fallback_providers: true, pinned_provider: '',
}
let secrets: SecretsStatus = { openrouter: true, huggingface: true, hf_user: 'k3nn3dy' }

function filterRows(rows: RowItem[], q: RowQuery): RowItem[] {
  return rows.filter((r) => {
    if (q.status && q.status !== 'all' && r.status !== q.status) return false
    if (q.leaf && r.metadata.leaf_id !== q.leaf) return false
    if (q.min_score !== undefined && (r.metadata.judge?.score ?? 0) < q.min_score) return false
    if (q.flags && q.flags.length && !q.flags.every((f) => r.metadata.flags.includes(f))) return false
    if (q.q) { const n = q.q.toLowerCase(); if (!r.metadata.id.includes(n) && !r.messages.some((m) => m.content?.toLowerCase().includes(n))) return false }
    return true
  })
}

export const mockApi: DataApi = {
  listProjects: () => delay([...state.values()].map((s) => s.project)),
  getProject: (id) => delay(need(id).project),
  getSummary: (id) => delay(summary(need(id))),
  createProject: ({ preset, name, brief, data_types }) => {
    const cfg: Partial<ProjectConfig> = { data_types: data_types as ProjectConfig['data_types'] }
    const p = makeProject(`p_${Math.random().toString(36).slice(2, 8)}`, name, brief, preset, cfg)
    state.set(p.id, { project: p, tree: [], prompts: [], rows: [], pairs: [], stagesDone: 0, exports: [], restored: new Set() })
    return delay(p)
  },
  patchProject: (id, patch) => { const s = need(id); s.project = { ...s.project, ...patch, config: { ...s.project.config, ...(patch.config ?? {}) }, updated_at: Date.now() / 1000 }; return delay(s.project) },
  deleteProject: (id) => { state.delete(id); return delay(undefined) },
  getTaxonomy: (id) => delay(need(id).tree),
  putTaxonomy: (id, tree) => { need(id).tree = tree; return delay(tree) },
  getPrompts: (id, q) => { const s = need(id); let items = s.prompts; if (q.leaf) items = items.filter((p) => p.leaf_id === q.leaf); if (q.q) items = items.filter((p) => p.text.toLowerCase().includes(q.q!.toLowerCase())); return delay(paged(items, q.page, 500)) },
  resamplePrompts: (id, leaf) => { const s = need(id); s.prompts = s.prompts.map((p) => (p.leaf_id === leaf ? { ...p, text: p.text.replace(/\bhost\b|web-03|db-primary|batch-07/g, 'api-11') } : p)); return delay(undefined, 600) },
  getRows: (id, q) => delay(paged(filterRows(need(id).rows, q), q.page, q.page_size ?? 50)),
  getRow: (id, rid) => { const r = need(id).rows.find((x) => x.metadata.id === rid); if (!r) throw new Error('row not found'); return delay(r) },
  patchRow: (id, rid, patch) => {
    const s = need(id); const i = s.rows.findIndex((x) => x.metadata.id === rid); if (i < 0) throw new Error('row not found')
    const r = s.rows[i]; const flags = new Set(r.metadata.flags)
    let status = r.status
    if (patch.action === 'accept') { status = 'accepted'; flags.delete('flagged') }
    if (patch.action === 'flag') { status = 'flagged'; flags.add('flagged') }
    if (patch.messages) { status = 'edited'; flags.add('edited') }
    s.rows[i] = { ...r, status, messages: patch.messages ?? r.messages, metadata: { ...r.metadata, flags: [...flags] } }
    return delay(s.rows[i])
  },
  bulkRows: (id, ids, action) => { const s = need(id); s.rows = s.rows.map((r) => (ids.includes(r.metadata.id) ? { ...r, status: action === 'accept' ? 'accepted' : action === 'flag' ? 'flagged' : r.status } : r)); return delay(undefined) },
  getPairs: (id) => delay(paged(need(id).pairs, 1, 500)),
  getRefusals: (id) => delay(paged(need(id).rows.filter((r) => r.status === 'refusal'), 1, 500)),
  getRefusalMatrix: (id) => {
    const cells = new Map<string, RefusalCell>()
    for (const r of need(id).rows) {
      const key = `${r.metadata.models.responses}|${r.metadata.leaf_path[0]}`
      const c = cells.get(key) ?? { model: r.metadata.models.responses, topic: r.metadata.leaf_path[0], refusals: 0, total: 0 }
      c.total++; if (r.status === 'refusal') c.refusals++; cells.set(key, c)
    }
    return delay([...cells.values()])
  },
  getJudgeSummary: (id) => {
    const s = need(id); const scored = s.rows.map((r) => r.metadata.judge?.score ?? 0)
    const histogram = new Array<number>(10).fill(0); scored.forEach((v) => histogram[Math.min(9, Math.floor(v / 0.5))]++)
    const teacher = s.project.config.responses.ensemble[0]?.slug ?? ''; const judge = s.project.config.judge.model.slug
    const out: JudgeSummary = { judged: scored.length, mean: scored.length ? scored.reduce((a, b) => a + b, 0) / scored.length : 0, histogram, ties: s.pairs.filter((p) => p.metadata.judge?.verdict === 'tie').length, below_threshold: scored.filter((v) => v < s.project.config.judge.low_score_threshold).length, family_warning: modelFamily(teacher) === modelFamily(judge) ? `Judge ${judge} shares a family with teacher ${teacher}; scores may be self-preferential.` : null }
    return delay(out)
  },
  getFilterSummary: (id) => {
    const s = need(id); const f = s.project.config.filters
    const removed = s.rows.filter((r) => r.status === 'filtered'); const refusals = s.rows.filter((r) => r.status === 'refusal')
    const count = (pref: string) => removed.filter((r) => r.filter_reason?.startsWith(pref)).length
    const out: FilterSummary = { rules: [
      { name: 'exact_dup', label: 'Exact duplicate', enabled: f.exact_dup, removed: count('exact_dup') },
      { name: 'near_dup', label: `Near duplicate (cosine ≥ ${f.near_dup_threshold})`, enabled: f.near_dup, removed: count('near_dup') },
      { name: 'refusal', label: 'Refusal → bucket', enabled: f.refusal, removed: refusals.length },
      { name: 'pii', label: 'PII (email, public IPv4, NI number)', enabled: f.pii, removed: count('pii') },
      { name: 'length', label: `Length ${f.min_chars}–${f.max_chars} chars`, enabled: f.length, removed: count('length') },
      { name: 'language', label: `Language = ${f.expected_language}`, enabled: f.language, removed: count('language') },
    ], removed, refusals, kept: s.rows.length - removed.length - refusals.length }
    return delay(out)
  },
  runFilter: (id, cfg) => { const s = need(id); s.project.config.filters = cfg; return mockApi.getFilterSummary(id) },
  restoreRows: (id, ids) => { const s = need(id); s.rows = s.rows.map((r) => (ids.includes(r.metadata.id) ? { ...r, status: 'accepted', filter_reason: null } : r)); return delay(undefined) },
  estimate: (id, stage) => { const s = need(id); const calls = Math.max(12, stage === 1 ? 4 : stage === 2 ? flattenLeaves(s.tree).length || 20 : s.prompts.length || 80); const est: Estimate = { est_usd: Math.round(calls * (stage === 5 ? 0.011 : 0.018) * 100) / 100, calls, est_tokens_in: calls * 1400, est_tokens_out: calls * 620, over_cap: false }; est.over_cap = s.project.spend_usd + est.est_usd > s.project.budget_cap_usd; return delay(est, 500) },
  runStage: (id, stage) => { const s = need(id); const live = liveMockRun(id); if (live) throw new ApiError(409, { code: 'run_conflict', message: `run ${live.id} is already running`, stage: live.stage, run_id: live.id }); const total = stage === 1 ? 4 : stage === 2 ? Math.max(20, flattenLeaves(s.tree).length) : Math.max(40, s.prompts.length); const r = startMockRun(stage, total, id); return delay({ run_id: r.id }, 300) },
  getRun: (runId) => { const r = getMockRun(runId); if (!r) throw new Error('run not found'); const run: Run = { id: r.id, project_id: r.projectId, stage: r.stage as StageNumber, status: r.status, params: {}, done: r.done0 + r.calls.length, total: r.total, errors: r.calls.filter((c) => c.status === 'error').length, refusals: r.calls.filter((c) => c.status === 'refusal').length, spend_usd: r.calls.reduce((a, c) => a + c.cost_usd, 0), est_usd: 1.2, started_at: r.started / 1000, partial: r.partial } as Run; return delay(run) },
  cancelRun: (runId) => { cancelMockRun(runId); for (const s of state.values()) if (s.paused?.runId === runId) s.paused = null; return delay(undefined) },
  resumeRun: (runId, force) => { resumeMockRun(runId, force); for (const s of state.values()) if (s.paused?.runId === runId) s.paused = null; return delay(undefined, 300) },
  getRunLog: (runId) => { const r = getMockRun(runId); const calls: RawCall[] = r ? [...r.calls].reverse() : []; return delay(paged(calls, 1, 200)) },
  subscribeRun: (runId, onEvent) => subscribeMockRun(runId, onEvent),
  exportBundle: (id, body) => {
    const s = need(id); const acc = s.rows.filter((r) => r.status === 'accepted' || r.status === 'edited').length; const ev = Math.round(acc * body.eval_split)
    const d = new Date()
    const p2 = (n: number) => String(n).padStart(2, '0')
    const stamp = `${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}`
    const rec: ExportRecord = { id: `x${Date.now()}`, created_at: Date.now() / 1000, formats: body.formats, path: `~/.dataset-genie/exports/${s.project.slug}/${stamp}`, rows_train: acc - ev, rows_eval: ev, hf_url: body.push ? `https://huggingface.co/datasets/${body.push.repo_id}` : null, status: 'ok', counts: { [body.formats[0]]: { train: acc - ev, eval: ev, total: acc } }, warnings: [], gated_out: body.gate_on_score ? 3 : 0 }
    s.exports.unshift(rec); return delay(rec, 900)
  },
  listExports: (id) => delay(need(id).exports),
  reviewStats: (id) => { const s = need(id); const by: Record<string, number> = {}; const flags: Record<string, number> = {}; for (const r of s.rows) { by[r.status] = (by[r.status] ?? 0) + 1; for (const f of r.metadata.flags) flags[f] = (flags[f] ?? 0) + 1 } const out: ReviewStats = { total: s.rows.length, by_status: by, flags, edited: flags.edited ?? 0, pairs: s.pairs.length, exportable: s.rows.filter((r) => r.status !== 'filtered' && r.status !== 'refusal').length }; return delay(out) },
  hfStatus: () => delay({ has_token: secrets.huggingface, username: secrets.hf_user ?? null } as HFStatus),
  models: (q) => delay(searchModels(q)),
  refreshModels: () => delay(MODEL_CATALOGUE as ModelInfo[], 800),
  getSettings: () => delay(settings),
  putSettings: (s) => { settings = s; return delay(settings) },
  secretsStatus: () => delay(secrets),
  setSecret: (name) => { secrets = { ...secrets, [name]: true }; return delay(undefined, 300) },
  deleteSecret: (name) => { secrets = { ...secrets, [name]: false, ...(name === 'huggingface' ? { hf_user: null } : {}) }; return delay(undefined) },
  presets: () => delay(PRESETS as Preset[]),
}

export { defaultConfig }
