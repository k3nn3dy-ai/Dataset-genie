import { create } from 'zustand'

// Mock is forced by `VITE_MOCK=1` or `?mock=1` (remembered for the tab so client-side navigation keeps it).
function initialMock(): boolean {
  if (import.meta.env.VITE_MOCK === '1') return true
  try {
    const q = new URLSearchParams(window.location.search).get('mock')
    if (q === '1') { sessionStorage.setItem('genie.mock', '1'); return true }
    if (q === '0') { sessionStorage.removeItem('genie.mock'); return false }
    return sessionStorage.getItem('genie.mock') === '1'
  } catch { return false }
}

interface UIState {
  activeProjectId: string | null
  activeRuns: Record<string, string> // `${projectId}:${stage}` -> runId
  mock: boolean
  mockReason: string | null
  sseError: boolean
  setActiveProject: (id: string | null) => void
  setActiveRun: (projectId: string, stage: number, runId: string | null) => void
  setMock: (on: boolean, reason?: string) => void
  setSseError: (v: boolean) => void
}

export const useStore = create<UIState>((set) => ({
  activeProjectId: null,
  activeRuns: {},
  mock: initialMock(),
  mockReason: initialMock() ? 'forced' : null,
  sseError: false,
  setActiveProject: (id) => set({ activeProjectId: id }),
  setActiveRun: (projectId, stage, runId) => set((s) => {
    const next = { ...s.activeRuns }
    const key = `${projectId}:${stage}`
    if (runId) next[key] = runId
    else delete next[key]
    return { activeRuns: next }
  }),
  setMock: (on, reason) => set({ mock: on, mockReason: on ? (reason ?? 'forced') : null }),
  setSseError: (v) => set({ sseError: v }),
}))

export const runKey = (projectId: string, stage: number): string => `${projectId}:${stage}`
