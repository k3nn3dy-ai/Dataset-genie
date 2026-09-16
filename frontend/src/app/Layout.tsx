import { Outlet, useParams } from 'react-router-dom'
import { useEffect } from 'react'
import { Rail } from './Rail'
import { useStore } from './store'

export function Layout() {
  const { projectId } = useParams()
  const setActive = useStore((s) => s.setActiveProject)
  useEffect(() => { if (projectId) setActive(projectId) }, [projectId, setActive])

  return (
    <div className="relative min-h-screen bg-bg text-text font-ui">
      <div className="relative z-10 flex min-h-screen">
        <Rail />
        <main className="relative flex-1 min-w-0">
          <div className="relative z-10 px-8 pt-6 pb-16 max-w-[1560px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
