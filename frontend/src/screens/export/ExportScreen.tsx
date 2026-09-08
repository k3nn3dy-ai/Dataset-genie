import { useState } from 'react'
import { useParams } from 'react-router-dom'
import type { ExportFormat, TemplateName } from '../../lib/types'
import { useExport, useExports, useHfStatus, useReviewStats } from '../../lib/queries'
import { StageScreen } from '../shared/StageScreen'
import { useConfigSection } from '../shared/useConfigSection'
import { Banner, Button, Chip, EmptyState, Field, Panel, Select, Slider, Spinner, StatTile, Toggle } from '../../components'
import { BundleTree, FormatCards, HFPanel } from './ExportPanels'
import { fmtTime, relTime } from '../../lib/format'

export function ExportScreen() {
  const { projectId } = useParams()
  const { draft, setDraft, loading, projectError, refetch, project } = useConfigSection(projectId, 'export')
  const exports = useExports(projectId)
  const stats = useReviewStats(projectId)
  const hf = useHfStatus(projectId)
  const run = useExport(projectId)
  const [lastPush, setLastPush] = useState<string | null>(null)
  const accepted = stats.data?.exportable ?? 0
  const evalRows = draft ? Math.round(accepted * draft.eval_split) : 0
  const blocked = stats.data && accepted === 0 ? { title: 'Nothing to export', body: 'Export ships accepted rows. Generate and review rows first.', stage: 3 as const } : null
  const missingTools = draft?.formats.includes('tools') && (project?.config.tools_schemas.length ?? 0) === 0

  const doExport = (push: boolean) => {
    if (!draft) return
    run.mutate({ formats: draft.formats, eval_split: draft.eval_split, stratify_by: draft.stratify_by, validate_template: draft.validate_template, include_judge_scores: draft.include_judge_scores, gate_on_score: draft.gate_on_score, gate_threshold: draft.gate_threshold, seed: draft.seed, push: push ? draft.hf : null }, { onSuccess: (r) => setLastPush(r.hf_url ?? null) })
  }

  const config = draft ? (
    <>
      <Panel title="Formats" actions={<span className="font-mono text-[10px] text-dim">{draft.formats.length} selected</span>}>
        <FormatCards selected={draft.formats} dataTypes={project?.data_types ?? []} onChange={(formats: ExportFormat[]) => setDraft({ formats })} />
        {missingTools && <Banner tone="amber" className="mt-3">`tools` format needs tool JSON schemas on the project; none are set.</Banner>}
      </Panel>
      <Panel title="Split & validation">
        <div className="flex flex-col gap-4">
          <Slider label="eval split" value={draft.eval_split} min={0} max={0.3} step={0.01} format={(v) => `${Math.round(v * 100)}% · ${evalRows} rows`} onChange={(eval_split) => setDraft({ eval_split })} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="stratify by"><Select value={draft.stratify_by} onChange={(e) => setDraft({ stratify_by: e.target.value as typeof draft.stratify_by })}><option value="leaf">leaf</option><option value="topic">topic</option><option value="difficulty">difficulty</option><option value="none">none</option></Select></Field>
            <Field label="seed"><Select value={draft.seed} onChange={(e) => setDraft({ seed: Number(e.target.value) })}>{[42, 7, 1337, 2024].map((s) => <option key={s} value={s}>{s}</option>)}</Select></Field>
          </div>
          <Toggle checked={draft.validate_template !== null} onChange={(v) => setDraft({ validate_template: v ? 'llama-3.1' : null })} label="Validate against chat template" hint="Renders every row; failure aborts the export loudly" />
          {draft.validate_template && (
            <Select value={draft.validate_template} onChange={(e) => setDraft({ validate_template: e.target.value as TemplateName })}><option value="llama-3.1">Llama 3.1</option><option value="chatml">ChatML (Qwen)</option><option value="gemma">Gemma (system folded into user)</option></Select>
          )}
          <Toggle checked={draft.include_judge_scores} onChange={(include_judge_scores) => setDraft({ include_judge_scores })} label="Include judge scores in metadata" />
          <Toggle checked={draft.gate_on_score} tone="amber" onChange={(gate_on_score) => setDraft({ gate_on_score })} label={`Gate export on score ≥ ${draft.gate_threshold.toFixed(1)}`} hint="Scores are visible, never gating by default" />
        </div>
      </Panel>
      <HFPanel hf={draft.hf} onChange={(hf) => setDraft({ hf })} status={hf.data} />
    </>
  ) : <Spinner />

  const results = (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-4 gap-2">
        <StatTile size="sm" label="exportable rows" value={accepted} tone="orange" hint={stats.data ? `${stats.data.by_status.accepted ?? 0} accepted · ${stats.data.by_status.edited ?? 0} edited · ${stats.data.by_status.draft ?? 0} draft` : undefined} />
        <StatTile size="sm" label="train / eval" value={accepted - evalRows} unit={`/ ${evalRows}`} tone="ok" />
        <StatTile size="sm" label="formats" value={draft?.formats.length ?? 0} hint={draft?.formats.join(', ')} />
        <StatTile size="sm" label="template" value={<span className="text-[15px]">{draft?.validate_template ?? 'none'}</span>} />
      </div>
      <Panel title="Bundle contents">{draft && project ? <BundleTree slug={project.slug} formats={draft.formats} /> : <Spinner />}</Panel>
      <div className="flex items-center gap-2">
        <Button variant="primary" icon="export" size="lg" loading={run.isPending && !run.variables?.push} disabled={!draft || draft.formats.length === 0 || !!blocked} onClick={() => doExport(false)} data-testid="run-stage">Export bundle</Button>
        <Button variant="steel" icon="upload" size="lg" loading={run.isPending && !!run.variables?.push} disabled={!draft || draft.formats.length === 0 || !!blocked || !hf.data?.has_token || !draft.hf.repo_id} onClick={() => doExport(true)} data-testid="export-push">Export and push to Hub</Button>
      </div>
      {run.error ? <Banner tone="red">Export failed: {run.error.message}</Banner> : null}
      {run.isSuccess && <Banner tone="ok" icon="check">Bundle written to <span className="font-mono">{run.data.path}</span> · {run.data.rows_train}/{run.data.rows_eval} train/eval{run.data.gated_out ? ` · ${run.data.gated_out} gated out` : ''}{lastPush && <> · pushed to <a className="underline text-orange" href={lastPush} target="_blank" rel="noreferrer">{lastPush}</a></>}</Banner>}
      {run.isSuccess && (run.data.warnings ?? []).map((w, i) => <Banner key={i} tone="amber">{w}</Banner>)}
      <Panel title="Previous exports" padded={false}>
        {exports.isLoading ? <div className="p-3"><Spinner /></div> : (exports.data ?? []).length === 0 ? <div className="p-4"><EmptyState title="No exports yet" body="Bundles land under ~/.dataset-genie/exports/<slug>/<timestamp>/." className="!py-8" /></div> : (
          <ul className="hairline">
            {(exports.data ?? []).map((x) => (
              <li key={x.id} className="px-4 py-2.5 flex items-center gap-3 font-mono text-[11.5px]">
                <span className="text-dim shrink-0" title={fmtTime(x.created_at)}>{relTime(x.created_at)}</span>
                <span className="truncate flex-1 text-text/85">{x.path}</span>
                <span className="text-muted shrink-0" title="train / eval of first format">{x.rows_train}/{x.rows_eval}</span>
                <div className="flex gap-1 shrink-0">{x.formats.map((f) => <Chip key={f} tone="orange">{f}</Chip>)}</div>
                {x.hf_url ? <a href={x.hf_url} target="_blank" rel="noreferrer" className="text-steel hover:underline shrink-0">hub ↗</a> : <span className="text-dim shrink-0">local</span>}
                <Chip tone={x.status === 'ok' ? 'ok' : 'red'}>{x.status}</Chip>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )

  return (
    <StageScreen stage={8} params={{}} noRun config={config} results={results} resultsCount={exports.data?.length} blocked={blocked} loading={loading} error={projectError ?? exports.error} onRetry={() => { void refetch(); void exports.refetch() }}
      idleHint="export writes JSONL + dataset_card.md + generation_config.yaml + manifest.json" subtitle="One JSON object per line, UTF-8, validated before a single byte is written. Reproducible from generation_config.yaml." />
  )
}
