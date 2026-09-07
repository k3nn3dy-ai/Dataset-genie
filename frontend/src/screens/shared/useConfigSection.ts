import { useCallback, useEffect, useRef, useState } from 'react'
import type { ProjectConfig } from '../../lib/types'
import { usePatchConfig, useProject } from '../../lib/queries'

/**
 * Local editable draft of one config section, auto-saved (debounced) via PATCH /projects/{id}.
 * Returns [draft, setDraft, meta]. `setDraft` accepts a partial or an updater.
 */
export function useConfigSection<K extends keyof ProjectConfig>(projectId: string | undefined, key: K) {
  const project = useProject(projectId)
  const patch = usePatchConfig(projectId)
  const [draft, setLocal] = useState<ProjectConfig[K] | null>(null)
  const timer = useRef<number | null>(null)
  const dirty = useRef(false)

  useEffect(() => {
    if (project.data && !dirty.current) setLocal(project.data.config[key])
  }, [project.data, key])

  const setDraft = useCallback((upd: Partial<ProjectConfig[K]> | ((d: ProjectConfig[K]) => ProjectConfig[K])) => {
    setLocal((d) => {
      if (d === null) return d
      const next = typeof upd === 'function' ? upd(d) : (Object.assign({}, d, upd) as ProjectConfig[K])
      dirty.current = true
      if (timer.current) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        dirty.current = false
        patch.mutate({ [key]: next } as Partial<ProjectConfig>)
      }, 700)
      return next
    })
  }, [key, patch])

  return { draft, setDraft, saving: patch.isPending, error: patch.error, project: project.data, loading: project.isLoading, projectError: project.error, refetch: project.refetch }
}

/** Same as above but for top-level project fields (budget, concurrency...). */
export function useTopLevelConfig(projectId: string | undefined) {
  const project = useProject(projectId)
  const patch = usePatchConfig(projectId)
  const set = (p: Partial<Pick<ProjectConfig, 'budget_cap_usd' | 'stop_at_pct' | 'concurrency' | 'prefer_prompt_caching' | 'allow_fallback_providers' | 'data_types'>>) => patch.mutate(p)
  return { config: project.data?.config, set, saving: patch.isPending }
}
