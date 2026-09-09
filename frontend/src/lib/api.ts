// Thin typed fetch wrapper + SSE subscription. All backend calls go through here.
import type { RunEvent } from './types'

const BASE = '/api'

export class ApiError extends Error {
  status: number
  detail: unknown
  /** True when the body was the backend's own `{detail}` JSON — i.e. the app answered, not a proxy in front of it. */
  fromBackend: boolean
  constructor(status: number, detail: unknown, fromBackend = false) {
    super(describeDetail(status, detail))
    this.status = status
    this.detail = detail
    this.fromBackend = fromBackend
  }
}

/**
 * Human-readable message for a backend `detail`. FastAPI sends a string for plain errors; our
 * export endpoint sends `{message, total, issues: [{id, reason}]}` for validation failures, so
 * surface the message plus the first few issues instead of a bare "API error 422".
 */
function describeDetail(status: number, detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const d = detail as { message?: unknown; total?: unknown; issues?: unknown }
    const parts: string[] = []
    if (typeof d.message === 'string') parts.push(d.message)
    if (Array.isArray(d.issues) && d.issues.length) {
      const shown = d.issues.slice(0, 3).map((i) => {
        const it = i as { id?: unknown; reason?: unknown }
        return typeof it.id === 'string' && typeof it.reason === 'string' ? `${it.id}: ${it.reason}` : JSON.stringify(i)
      })
      const total = typeof d.total === 'number' ? d.total : d.issues.length
      const more = total > shown.length ? ` (+${total - shown.length} more)` : ''
      parts.push(`${total} issue(s): ${shown.join('; ')}${more}`)
    }
    if (parts.length) return parts.join(' — ')
  }
  return `API error ${status}`
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail: unknown = res.statusText
    let fromBackend = false
    try {
      const parsed = await res.json()
      if (parsed && typeof parsed === 'object' && 'detail' in parsed) { detail = parsed.detail; fromBackend = true }
    } catch { /* non-JSON body: a proxy or gateway answered, not the backend */ }
    throw new ApiError(res.status, detail, fromBackend)
  }
  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') ?? ''
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}

/** Subscribe to a run's SSE stream. Returns an unsubscribe function. */
export function subscribeRun(runId: string, onEvent: (ev: RunEvent) => void, onError?: (e: Event) => void): () => void {
  const es = new EventSource(`${BASE}/runs/${runId}/events`)
  const handler = (e: MessageEvent) => onEvent(JSON.parse(e.data) as RunEvent)
  for (const t of ['progress', 'worker', 'log', 'item', 'done']) es.addEventListener(t, handler as EventListener)
  es.onerror = (e) => onError?.(e)
  es.addEventListener('done', () => es.close())
  return () => es.close()
}
