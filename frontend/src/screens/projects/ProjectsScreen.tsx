import { useState } from 'react'
import { Header } from '../../app/Header'
import { useDeleteProject, useProjects } from '../../lib/queries'
import { Button, ConfirmModal, EmptyState, ErrorState, Spinner, StatTile } from '../../components'
import { ProjectCard } from './ProjectCard'
import { NewProjectModal } from './NewProjectModal'
import { usd } from '../../lib/format'

export function ProjectsScreen() {
  const projects = useProjects()
  const del = useDeleteProject()
  const [open, setOpen] = useState(false)
  const [toDelete, setToDelete] = useState<{ id: string; name: string } | null>(null)
  const list = projects.data ?? []
  const totalSpend = list.reduce((a, p) => a + p.spend_usd, 0)

  return (
    <>
      <Header
        kicker="STAGE 00 · CHOOSE YOUR DOMAIN" title="Projects" numeral="00"
        subtitle="Each project is one dataset: a brief, a taxonomy, eight re-runnable stages and a hard budget cap."
        actions={<Button variant="primary" size="lg" icon="plus" onClick={() => setOpen(true)} data-testid="run-stage">New project</Button>}
      />
      {projects.isLoading && <Spinner />}
      {projects.error && <ErrorState error={projects.error} onRetry={() => projects.refetch()} />}
      {projects.data && list.length === 0 && (
        <EmptyState icon="folder" title="No projects yet" body="Create a project from a preset. You can edit every stage's configuration before running it." action={{ label: 'New project', icon: 'plus', onClick: () => setOpen(true) }} />
      )}
      {list.length > 0 && (
        <>
          <div className="grid grid-cols-4 gap-3 mb-5">
            <StatTile label="projects" value={list.length} tone="orange" />
            <StatTile label="total spend" value={usd(totalSpend)} tone="steel" />
            <StatTile label="data types" value={[...new Set(list.flatMap((p) => p.data_types))].join(' · ')} size="sm" />
            <StatTile label="mode" value={<span className="text-[15px]">local · OpenRouter</span>} size="sm" hint="~/.dataset-genie" />
          </div>
          <div className="grid grid-cols-3 gap-4">
            {list.map((p) => <ProjectCard key={p.id} project={p} onDelete={() => setToDelete({ id: p.id, name: p.name })} />)}
          </div>
        </>
      )}
      <NewProjectModal open={open} onClose={() => setOpen(false)} />
      <ConfirmModal
        open={!!toDelete} onClose={() => setToDelete(null)} danger confirmLabel="Delete project" loading={del.isPending} title="Delete project"
        body={<>Delete <span className="text-text font-semibold">{toDelete?.name}</span> and all of its rows, pairs, runs and raw calls? Exported bundles on disk are kept.</>}
        onConfirm={() => { if (toDelete) del.mutate(toDelete.id, { onSuccess: () => setToDelete(null) }) }}
      />
    </>
  )
}
