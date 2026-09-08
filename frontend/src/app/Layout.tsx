import { useEffect } from 'react'
import { Outlet, useParams } from 'react-router-dom'
import { Atmosphere } from './Atmosphere'
import { Lattice } from './Lattice'
import { Rail } from './Rail'
import { useStore } from './store'

export function Layout() {
  const { projectId } = useParams()
  const setActive = useStore((s) => s.setActiveProject)
  useEffect(() => { if (projectId) setActive(projectId) }, [projectId, setActive])

  return (
    <div className="relative min-h-screen bg-bg text-text font-ui">
      <Atmosphere />
      <div className="relative z-10 flex min-h-screen">
        <Rail />
        <main className="relative flex-1 min-w-0">
          {/* Lattice backdrop, fixed to the main column, masked under the title */}
          <Lattice className="fixed top-0 bottom-0 left-[236px] right-0 z-0 pointer-events-none opacity-90" />
          <div className="relative z-10 px-8 pt-6 pb-16 max-w-[1560px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
