// TanStack Query hooks. Every screen reads/writes through these; swapping endpoints happens in data.ts.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { FilterConfig, Message, ProjectConfig, TopicNode } from './types'
import type { SettingsData } from './viewtypes'
import { data, type ExportRequest, type RowAction, type RowQuery } from './data'

export const keys = {
  projects: ['projects'] as const,
  project: (id: string) => ['project', id] as const,
  summary: (id: string) => ['summary', id] as const,
  taxonomy: (id: string) => ['taxonomy', id] as const,
  prompts: (id: string, q: object) => ['prompts', id, q] as const,
  rows: (id: string, q: object) => ['rows', id, q] as const,
  row: (id: string, rid: string) => ['row', id, rid] as const,
  pairs: (id: string) => ['pairs', id] as const,
  refusals: (id: string) => ['refusals', id] as const,
  refusalMatrix: (id: string) => ['refusal-matrix', id] as const,
  judge: (id: string) => ['judge', id] as const,
  filter: (id: string) => ['filter', id] as const,
  exports: (id: string) => ['exports', id] as const,
  run: (runId: string) => ['run', runId] as const,
  runLog: (runId: string) => ['run-log', runId] as const,
  models: (q: string) => ['models', q] as const,
  settings: ['settings'] as const,
  secrets: ['secrets'] as const,
  presets: ['presets'] as const,
}

export const useProjects = () => useQuery({ queryKey: keys.projects, queryFn: () => data.listProjects() })
export const useProject = (id: string | undefined) => useQuery({ queryKey: keys.project(id ?? ''), queryFn: () => data.getProject(id!), enabled: !!id })
export const useSummary = (id: string | undefined) => useQuery({ queryKey: keys.summary(id ?? ''), queryFn: () => data.getSummary(id!), enabled: !!id })
export const useTaxonomy = (id: string | undefined) => useQuery({ queryKey: keys.taxonomy(id ?? ''), queryFn: () => data.getTaxonomy(id!), enabled: !!id })
export const usePrompts = (id: string | undefined, q: { leaf?: string; q?: string; page?: number } = {}) => useQuery({ queryKey: keys.prompts(id ?? '', q), queryFn: () => data.getPrompts(id!, q), enabled: !!id })
export const useRows = (id: string | undefined, q: RowQuery = {}) => useQuery({ queryKey: keys.rows(id ?? '', q), queryFn: () => data.getRows(id!, q), enabled: !!id })
export const useRow = (id: string | undefined, rid: string | null) => useQuery({ queryKey: keys.row(id ?? '', rid ?? ''), queryFn: () => data.getRow(id!, rid!), enabled: !!id && !!rid })
export const usePairs = (id: string | undefined) => useQuery({ queryKey: keys.pairs(id ?? ''), queryFn: () => data.getPairs(id!), enabled: !!id })
export const useRefusals = (id: string | undefined) => useQuery({ queryKey: keys.refusals(id ?? ''), queryFn: () => data.getRefusals(id!), enabled: !!id })
export const useRefusalMatrix = (id: string | undefined) => useQuery({ queryKey: keys.refusalMatrix(id ?? ''), queryFn: () => data.getRefusalMatrix(id!), enabled: !!id })
export const useJudgeSummary = (id: string | undefined) => useQuery({ queryKey: keys.judge(id ?? ''), queryFn: () => data.getJudgeSummary(id!), enabled: !!id })
export const useFilterSummary = (id: string | undefined) => useQuery({ queryKey: keys.filter(id ?? ''), queryFn: () => data.getFilterSummary(id!), enabled: !!id })
export const useExports = (id: string | undefined) => useQuery({ queryKey: keys.exports(id ?? ''), queryFn: () => data.listExports(id!), enabled: !!id })
export const useRun = (runId: string | null) => useQuery({ queryKey: keys.run(runId ?? ''), queryFn: () => data.getRun(runId!), enabled: !!runId })
export const useRunLog = (runId: string | null, live: boolean) => useQuery({ queryKey: keys.runLog(runId ?? ''), queryFn: () => data.getRunLog(runId!), enabled: !!runId, refetchInterval: live ? 1500 : false })
export const useModels = (q = '') => useQuery({ queryKey: keys.models(q), queryFn: () => data.models(q), staleTime: 5 * 60_000 })
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: () => data.getSettings() })
export const useSecrets = () => useQuery({ queryKey: keys.secrets, queryFn: () => data.secretsStatus() })
export const usePresets = () => useQuery({ queryKey: keys.presets, queryFn: () => data.presets() })

/** Invalidate everything under a project after a mutation. */
function useInvalidateProject() {
  const qc = useQueryClient()
  return (id: string) => {
    void qc.invalidateQueries({ queryKey: keys.projects })
    void qc.invalidateQueries({ predicate: (q) => Array.isArray(q.queryKey) && q.queryKey[1] === id })
  }
}

export function useCreateProject() {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (b: { preset: string; name: string; brief: string; data_types: string[] }) => data.createProject(b), onSuccess: (p) => inv(p.id) })
}
export function useDeleteProject() {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (id: string) => data.deleteProject(id), onSuccess: (_r, id) => inv(id) })
}
export function usePatchConfig(id: string | undefined) {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (config: Partial<ProjectConfig>) => data.patchProject(id!, { config }), onSuccess: () => inv(id!) })
}
export function usePutTaxonomy(id: string | undefined) {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (tree: TopicNode[]) => data.putTaxonomy(id!, tree), onSuccess: () => inv(id!) })
}
export function useResample(id: string | undefined) {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (leaf: string) => data.resamplePrompts(id!, leaf), onSuccess: () => inv(id!) })
}
export function usePatchRow(id: string | undefined) {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (v: { rid: string; messages?: Message[]; action?: RowAction }) => data.patchRow(id!, v.rid, { messages: v.messages, action: v.action }), onSuccess: () => inv(id!) })
}
export function useBulkRows(id: string | undefined) {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (v: { ids: string[]; action: RowAction }) => data.bulkRows(id!, v.ids, v.action), onSuccess: () => inv(id!) })
}
export function useRestoreRows(id: string | undefined) {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (ids: string[]) => data.restoreRows(id!, ids), onSuccess: () => inv(id!) })
}
export function useRunFilter(id: string | undefined) {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (cfg: FilterConfig) => data.runFilter(id!, cfg), onSuccess: () => inv(id!) })
}
export function useEstimate(id: string | undefined, stage: number) {
  return useMutation({ mutationFn: (params: Record<string, unknown>) => data.estimate(id!, stage, params) })
}
export function useRunStage(id: string | undefined, stage: number) {
  return useMutation({ mutationFn: (params: Record<string, unknown>) => data.runStage(id!, stage, params) })
}
export function useCancelRun() { return useMutation({ mutationFn: (runId: string) => data.cancelRun(runId) }) }
export function useExport(id: string | undefined) {
  const inv = useInvalidateProject()
  return useMutation({ mutationFn: (body: ExportRequest) => data.exportBundle(id!, body), onSuccess: () => inv(id!) })
}
export function usePutSettings() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (s: SettingsData) => data.putSettings(s), onSuccess: () => void qc.invalidateQueries({ queryKey: keys.settings }) })
}
export function useSetSecret() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (v: { name: 'openrouter' | 'huggingface'; value: string }) => data.setSecret(v.name, v.value), onSuccess: () => void qc.invalidateQueries({ queryKey: keys.secrets }) })
}
export function useDeleteSecret() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (name: 'openrouter' | 'huggingface') => data.deleteSecret(name), onSuccess: () => void qc.invalidateQueries({ queryKey: keys.secrets }) })
}
export function useRefreshModels() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: () => data.refreshModels(), onSuccess: () => void qc.invalidateQueries({ queryKey: ['models'] }) })
}
