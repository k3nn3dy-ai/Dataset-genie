import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import type { TopicNode } from '../../lib/types'
import { usePutTaxonomy, useTaxonomy } from '../../lib/queries'
import { StageScreen } from '../shared/StageScreen'
import { useConfigSection } from '../shared/useConfigSection'
import { Button, Chip, EmptyState, Field, ModelPicker, NumberInput, Panel, Slider, Spinner, StatTile, Toggle } from '../../components'
import { TreeEditor, countLeaves } from './TreeEditor'
import { num } from '../../lib/format'

export function TaxonomyScreen() {
  const { projectId } = useParams()
  const { draft, setDraft, loading, projectError, refetch } = useConfigSection(projectId, 'taxonomy')
  const tax = useTaxonomy(projectId)
  const put = usePutTaxonomy(projectId)
  const [tree, setTree] = useState<TopicNode[] | null>(null)
  const [dirty, setDirty] = useState(false)
  useEffect(() => { if (tax.data && !dirty) setTree(tax.data) }, [tax.data, dirty])
  const edit = (t: TopicNode[]) => { setTree(t); setDirty(true) }
  const save = () => { if (tree) put.mutate(tree, { onSuccess: () => setDirty(false) }) }

  const leaves = tree ? tree.reduce((a, n) => a + countLeaves(n), 0) : 0
  const negLeaves = tree ? tree.reduce((a, n) => a + countNeg(n), 0) : 0
  const rowsPerLeaf = draft?.rows_per_leaf ?? 8
  const projected = draft ? draft.topics * (draft.depth === 3 ? draft.subtopics_per_topic : 1) * draft.leaves_per_topic : 0

  const config = draft ? (
    <>
      <Panel title="Generator" kana="生成">
        <div className="flex flex-col gap-4">
          <ModelPicker label="taxonomy model" value={draft.model} onChange={(m) => setDraft({ model: m })} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="depth" hint="2–3"><NumberInput value={draft.depth} min={2} max={3} onChange={(v) => setDraft({ depth: v })} /></Field>
            <Field label="topics"><NumberInput value={draft.topics} min={1} max={30} onChange={(v) => setDraft({ topics: v })} /></Field>
            <Field label="subtopics / topic"><NumberInput value={draft.subtopics_per_topic} min={1} max={12} disabled={draft.depth < 3} onChange={(v) => setDraft({ subtopics_per_topic: v })} /></Field>
            <Field label="leaves / subtopic"><NumberInput value={draft.leaves_per_topic} min={1} max={20} onChange={(v) => setDraft({ leaves_per_topic: v })} /></Field>
          </div>
          <Slider label="rows per leaf" value={draft.rows_per_leaf} min={1} max={50} onChange={(v) => setDraft({ rows_per_leaf: v })} />
          <Toggle checked={draft.difficulty_tiers} onChange={(v) => setDraft({ difficulty_tiers: v })} label="Difficulty tiers" hint="Tag each leaf easy / medium / hard" />
          <Toggle checked={draft.negative_branches} onChange={(v) => setDraft({ negative_branches: v })} label="Negative branches" hint="Out-of-scope subtopics the model should refuse" tone="amber" />
          <Field label="task types" hint="cycle on leaf chips">
            <div className="flex flex-wrap gap-1">{draft.task_types.map((t) => <Chip key={t} tone="default" onRemove={() => setDraft({ task_types: draft.task_types.filter((x) => x !== t) })}>{t}</Chip>)}
              <button type="button" className="label hover:text-cyan px-1" onClick={() => { const t = window.prompt('Task type (uppercase)'); if (t) setDraft({ task_types: [...draft.task_types, t.toUpperCase()] }) }}>+ add</button>
            </div>
          </Field>
        </div>
      </Panel>
      <div className="grid grid-cols-2 gap-3">
        <StatTile label="target rows" value={num(leaves * rowsPerLeaf)} tone="cyan" hint={`${leaves} leaves × ${rowsPerLeaf}`} />
        <StatTile label="projected (config)" value={num(projected * rowsPerLeaf)} hint={`${projected} leaves from generator`} />
      </div>
    </>
  ) : <Spinner />

  const results = tree ? (
    tree.length === 0 ? (
      <EmptyState title="No taxonomy yet" kana="未生成" body="Run stage 01 to generate topics, subtopics and leaves from the brief — or add topics by hand and save." />
    ) : (
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 font-mono text-[11px] text-muted">
            <span><span className="text-magenta">{tree.length}</span> topics</span><span><span className="text-cyan">{leaves}</span> leaves</span><span><span className="text-amber">{negLeaves}</span> negative</span>
            {dirty && <Chip tone="amber">unsaved</Chip>}
          </div>
          <Button size="sm" variant={dirty ? 'primary' : 'ghost'} icon="check" disabled={!dirty} loading={put.isPending} onClick={save}>Save tree</Button>
        </div>
        <TreeEditor tree={tree} onChange={edit} taskTypes={draft?.task_types ?? ['TRIAGE']} depth={draft?.depth ?? 3} />
      </div>
    )
  ) : <Spinner />

  return (
    <StageScreen
      stage={1} params={{ ...(draft ?? {}) }} config={config} results={results} resultsCount={leaves} loading={loading || tax.isLoading}
      error={projectError ?? tax.error} onRetry={() => { void refetch(); void tax.refetch() }}
      subtitle="One structured call per depth level, so you can edit the tree between levels. Target rows = leaves × rows per leaf."
      extraActions={dirty ? <Button variant="outline" icon="check" onClick={save} loading={put.isPending}>Save tree</Button> : null}
    />
  )
}

function countNeg(n: TopicNode): number { return n.is_leaf ? (n.is_negative ? 1 : 0) : (n.children ?? []).reduce((a, c) => a + countNeg(c), 0) }
