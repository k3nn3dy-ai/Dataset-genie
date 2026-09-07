import { useParams } from 'react-router-dom'
import { useJudgeSummary, useRows } from '../../lib/queries'
import { StageScreen } from '../shared/StageScreen'
import { useConfigSection } from '../shared/useConfigSection'
import { Banner, Button, Chip, EmptyState, Histogram, IconButton, IdCell, Input, ModelPicker, MonoTable, TextCell, Panel, Slider, Spinner, StatTile, Toggle, toneFor } from '../../components'
import { modelFamily } from '../../lib/format'

export function JudgeScreen() {
  const { projectId } = useParams()
  const { draft, setDraft, loading, projectError, refetch, project } = useConfigSection(projectId, 'judge')
  const exp = useConfigSection(projectId, 'export')
  const summary = useJudgeSummary(projectId)
  const rows = useRows(projectId, { page_size: 2000 })
  const items = (rows.data?.items ?? []).filter((r) => r.metadata.judge)
  const rubricTotal = draft?.rubric.reduce((a, c) => a + c.weight, 0) ?? 0
  const teacher = project?.config.responses.ensemble[0]?.slug ?? ''
  const sameFamily = draft && teacher && modelFamily(draft.model.slug) === modelFamily(teacher)
  const blocked = rows.data && rows.data.total === 0 ? { title: 'Nothing to judge', body: 'The judge scores teacher responses. Run stage 03 first.', stage: 3 as const } : null
  const s = summary.data

  const config = draft ? (
    <>
      <Panel title="Judge model" kana="審査員">
        <ModelPicker value={draft.model} onChange={(model) => setDraft({ model })} warn={sameFamily ? `Same family as teacher (${modelFamily(teacher)})` : null} />
        {sameFamily && <Banner tone="amber" className="mt-3">Judge and teacher share the <span className="font-mono">{modelFamily(teacher)}</span> family. Models rate their own family higher; pick a different vendor for an honest second opinion.</Banner>}
      </Panel>
      <Panel title="Rubric" kana="基準" actions={<span className={`font-mono text-[10px] ${rubricTotal === 100 ? 'text-acid' : 'text-amber'}`}>weights {rubricTotal} / 100</span>}>
        <div className="flex flex-col gap-3">
          {draft.rubric.map((c, i) => (
            <div key={i} className="rounded-btn border border-line bg-bg/40 p-2.5 flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <Input mono={false} value={c.name} onChange={(e) => setDraft({ rubric: draft.rubric.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)) })} className="!h-7 flex-1 !font-semibold" />
                <span className="font-mono text-[12px] text-cyan tabular-nums w-10 text-right">{c.weight}</span>
                <IconButton icon="trash" label="Remove criterion" size="sm" onClick={() => setDraft({ rubric: draft.rubric.filter((_, k) => k !== i) })} />
              </div>
              <Input mono={false} value={c.description} onChange={(e) => setDraft({ rubric: draft.rubric.map((x, k) => (k === i ? { ...x, description: e.target.value } : x)) })} className="!h-7 !text-[12.5px]" placeholder="What a 5 looks like" />
              <Slider value={c.weight} min={0} max={100} onChange={(w) => setDraft({ rubric: draft.rubric.map((x, k) => (k === i ? { ...x, weight: w } : x)) })} />
            </div>
          ))}
          {rubricTotal !== 100 && <Banner tone="amber">Weights must sum to 100 (currently {rubricTotal}).</Banner>}
          <Button size="sm" variant="outline" icon="plus" className="self-start" onClick={() => setDraft({ rubric: [...draft.rubric, { name: 'New criterion', weight: Math.max(0, 100 - rubricTotal), description: '' }] })}>Add criterion</Button>
        </div>
      </Panel>
      <Panel title="Thresholds" kana="閾値">
        <div className="flex flex-col gap-4">
          <Slider label="low-score flag threshold" value={draft.low_score_threshold} min={0} max={5} step={0.1} format={(v) => v.toFixed(1)} tone="amber" onChange={(low_score_threshold) => setDraft({ low_score_threshold })} />
          <Toggle checked={draft.drop_ties_from_dpo} onChange={(drop_ties_from_dpo) => setDraft({ drop_ties_from_dpo })} label="Drop ties from DPO export" hint="Pairs the judge cannot separate are flagged and skipped" />
          {exp.draft && (
            <Toggle checked={exp.draft.gate_on_score} tone="amber" onChange={(gate_on_score) => exp.setDraft({ gate_on_score })} label="Gate export on score" hint="Scores are visible, never gating by default" />
          )}
        </div>
      </Panel>
    </>
  ) : <Spinner />

  const results = summary.isLoading ? <Spinner /> : !s || s.judged === 0 ? (
    <EmptyState title="No scores yet" kana="未審査" body="Run stage 05 to score every response 1–5 per criterion with a one-line rationale. Pairs are judged blind in random A/B order." />
  ) : (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-4 gap-2">
        <StatTile size="sm" label="judged" value={s.judged} tone="cyan" />
        <StatTile size="sm" label="mean score" value={s.mean.toFixed(2)} unit="/ 5" tone="acid" />
        <StatTile size="sm" label={`below ${draft?.low_score_threshold.toFixed(1) ?? '3.0'}`} value={s.below_threshold} tone={s.below_threshold > 0 ? 'amber' : 'default'} hint="flagged low_score, not removed" />
        <StatTile size="sm" label="ties" value={s.ties} tone="magenta" hint="excluded from DPO" />
      </div>
      {s.family_warning && <Banner tone="amber">{s.family_warning}</Banner>}
      <Panel title="Score distribution · 10 bins" kana="分布"><Histogram bins={s.histogram} threshold={draft?.low_score_threshold} mean={s.mean} /></Panel>
      <MonoTable rows={items} rowKey={(r) => r.metadata.id} maxHeight="300px" defaultSort={{ key: 'score', dir: 'asc' }}
        rowTone={(r) => ((r.metadata.judge?.score ?? 5) < (draft?.low_score_threshold ?? 3) ? 'amber' : 'default')}
        columns={[
          { key: 'id', header: 'id', render: (r) => <IdCell id={r.metadata.id} max={150} />, sortValue: (r) => r.metadata.id },
          { key: 'score', header: 'score', align: 'right', render: (r) => <span className={(r.metadata.judge?.score ?? 0) < (draft?.low_score_threshold ?? 3) ? 'text-amber' : 'text-acid'}>{r.metadata.judge?.score.toFixed(1)}</span>, sortValue: (r) => r.metadata.judge?.score ?? 0 },
          ...(draft?.rubric ?? []).slice(0, 4).map((c) => ({ key: c.name, header: <span title={c.name}>{c.name.slice(0, 4)}</span>, align: 'right' as const, render: (r: typeof items[number]) => <span className="text-muted">{r.metadata.judge?.criteria[c.name] ?? '—'}</span> })),
          { key: 'rationale', header: 'rationale', render: (r) => <TextCell max={150} className="text-text/75" text={r.metadata.judge?.rationale ?? ''} /> },
          { key: 'flags', header: 'flags', render: (r) => <div className="flex gap-1">{r.metadata.flags.map((f) => <Chip key={f} tone={toneFor(f)}>{f}</Chip>)}</div> },
        ]} />
    </div>
  )

  return (
    <StageScreen stage={5} params={{ ...(draft ?? {}) }} config={config} results={results} resultsCount={s?.judged} blocked={blocked} loading={loading} error={projectError ?? summary.error} onRetry={() => { void refetch(); void summary.refetch() }}
      subtitle="Weighted rubric, 1–5 per criterion, normalised to 0–5. Scores are visible everywhere and never gate export unless you say so." />
  )
}
