import { Outlet } from 'react-router-dom'

// Placeholder shell. Track A replaces this with Rail + Header + Atmosphere + Lattice.
export function Layout() {
  return (
    <div className="min-h-full flex bg-bg text-text font-ui">
      <aside className="w-[236px] shrink-0 border-r border-line p-4">
        <div className="font-display font-bold uppercase text-cyan">Dataset Genie</div>
        <div className="label mt-1">データセット・ジーニー</div>
      </aside>
      <main className="flex-1 p-8"><Outlet /></main>
    </div>
  )
}
