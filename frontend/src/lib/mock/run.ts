import type { RunEvent, RunStatus } from '../types'
import type { RawCall } from '../viewtypes'
import { mulberry32, pick } from '../rng'

// Simulated run: emits a believable SSE-like event stream over ~12 seconds.
export interface MockRunState { id: string; stage: number; total: number; status: RunStatus; started: number; cancelled: boolean; calls: RawCall[]; done0: number }
const runs = new Map<string, MockRunState>()
let counter = 100

export function startMockRun(stage: number, total: number): MockRunState {
  const id = `run_${(++counter).toString(36)}`
  const st: MockRunState = { id, stage, total, status: 'running', started: Date.now(), cancelled: false, calls: [], done0: 0 }
  runs.set(id, st)
  return st
}
/** A run that was interrupted part-way (status paused) — resumable from `done0`. */
export function seedPausedRun(id: string, stage: number, total: number, done: number): MockRunState {
  const st: MockRunState = { id, stage, total, status: 'paused', started: Date.now() - 600_000, cancelled: false, calls: [], done0: done }
  runs.set(id, st)
  return st
}
export function resumeMockRun(id: string): void { const r = runs.get(id); if (r) { r.status = 'running'; r.cancelled = false; r.started = Date.now() } }
export function getMockRun(id: string): MockRunState | undefined { return runs.get(id) }
export function cancelMockRun(id: string): void { const r = runs.get(id); if (r) r.cancelled = true }

const MODELS = ['anthropic/claude-sonnet-4', 'openai/gpt-4.1']
const MSGS = ['reserving budget for batch', 'structured output parsed', 'retry 1/3 after 429 from provider', 'refusal detected: short-answer heuristic', 'prompt cache hit (anthropic)', 'embedding 24 prompts for near-dup guard', 'worker pool at concurrency 8']

export function subscribeMockRun(runId: string, onEvent: (ev: RunEvent) => void): () => void {
  const st = runs.get(runId)
  if (!st) return () => undefined
  const rnd = mulberry32(counter * 31 + st.stage)
  let done = st.done0
  let refusals = 0
  let errors = 0
  let spend = 0
  const workers = 8
  const tickMs = 260
  const perTick = Math.max(1, Math.round(st.total / 45))
  onEvent({ type: 'log', level: 'info', ts: Date.now() / 1000, msg: `run ${runId} started · stage ${st.stage} · ${st.total} items` })
  for (let w = 0; w < workers; w++) onEvent({ type: 'worker', worker_id: w, status: 'idle' })
  const timer = setInterval(() => {
    if (st.cancelled) {
      st.status = 'cancelled'
      onEvent({ type: 'log', level: 'warn', ts: Date.now() / 1000, msg: 'cancel requested — draining in-flight calls' })
      onEvent({ type: 'done', status: 'cancelled' })
      clearInterval(timer)
      return
    }
    for (let i = 0; i < perTick && done < st.total; i++) {
      done++
      const r = rnd()
      const isRef = r < 0.04
      const isErr = r > 0.985
      if (isRef) refusals++
      if (isErr) errors++
      const cost = 0.004 + rnd() * 0.012
      spend += cost
      const model = pick(rnd, MODELS)
      const target = `linux-incident-triage-item-${String(done).padStart(4, '0')}`
      st.calls.push({ id: `c${done}`, ts: Date.now() / 1000, model, target_id: target, status: isErr ? 'error' : isRef ? 'refusal' : 'ok', tokens_in: 900 + Math.floor(rnd() * 600), tokens_out: 300 + Math.floor(rnd() * 500), cost_usd: cost, latency_ms: 1200 + Math.floor(rnd() * 3000), error: isErr ? 'provider timeout after 60s' : null })
      onEvent({ type: 'item', target_id: target, status: isErr ? 'error' : isRef ? 'refusal' : 'done' })
      const w = Math.floor(rnd() * workers)
      onEvent({ type: 'worker', worker_id: w, status: isErr ? 'error' : 'calling', target_id: target, model })
    }
    const mins = Math.max(0.05, (Date.now() - st.started) / 60000)
    onEvent({ type: 'progress', done, total: st.total, rows_per_min: done / mins, refusals, errors, spend_usd: spend, cap_usd: 15 })
    if (rnd() < 0.25) onEvent({ type: 'log', level: rnd() < 0.15 ? 'warn' : 'info', ts: Date.now() / 1000, msg: pick(rnd, MSGS) })
    if (done >= st.total) {
      st.status = 'done'
      for (let w = 0; w < workers; w++) onEvent({ type: 'worker', worker_id: w, status: 'done' })
      onEvent({ type: 'log', level: 'info', ts: Date.now() / 1000, msg: `run complete · ${done} items · $${spend.toFixed(2)}` })
      onEvent({ type: 'done', status: 'done' })
      clearInterval(timer)
    }
  }, tickMs)
  return () => clearInterval(timer)
}
