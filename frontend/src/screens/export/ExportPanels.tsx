import clsx from 'clsx'
import type { ExportFormat, HFPushConfig } from '../../lib/types'
import type { HFStatus } from '../../lib/viewtypes'
import { Chip, Field, Icon, Input, Panel, Segmented, Select } from '../../components'

const FORMATS: { key: ExportFormat; label: string; trainer: string; shape: string }[] = [
  { key: 'sft', label: 'SFT', trainer: 'Unsloth · SFTTrainer', shape: '{"messages":[…]}' },
  { key: 'alpaca', label: 'Alpaca', trainer: 'SFTTrainer · alpaca template', shape: '{"instruction","input","output"}' },
  { key: 'dpo', label: 'DPO / ORPO', trainer: 'DPOTrainer · ORPOTrainer', shape: '{"prompt","chosen","rejected"}' },
  { key: 'tools', label: 'Tool calling', trainer: 'SFTTrainer · tools=', shape: '{"messages":[…],"tools":[…]}' },
  { key: 'grpo', label: 'GRPO', trainer: 'GRPOTrainer', shape: '{"prompt":[…],"answer"}' },
]

export function FormatCards({ selected, onChange, dataTypes }: { selected: ExportFormat[]; onChange: (f: ExportFormat[]) => void; dataTypes: string[] }) {
  const toggle = (k: ExportFormat) => onChange(selected.includes(k) ? selected.filter((x) => x !== k) : [...selected, k])
  return (
    <div className="grid grid-cols-2 gap-2">
      {FORMATS.map((f) => {
        const on = selected.includes(f.key)
        const fits = f.key === 'alpaca' ? dataTypes.includes('sft') : dataTypes.includes(f.key)
        return (
          <button key={f.key} type="button" onClick={() => toggle(f.key)} className={clsx('text-left rounded-card border p-3 transition-colors focus-ring flex flex-col gap-1', on ? 'border-orange/60 bg-orange/10 shadow-[0_0_18px_rgba(255,106,26,.08)]' : 'border-line hover:border-line2', !fits && 'opacity-60')}>
            <div className="flex items-center justify-between"><span className={clsx('font-display font-bold uppercase text-[12.5px] tracking-[.05em]', on && 'text-orange')}>{f.label}</span><span className={clsx('w-3.5 h-3.5 rounded-[3px] border flex items-center justify-center', on ? 'bg-orange border-orange text-bg' : 'border-line2')}>{on && <Icon name="check" size={10} strokeWidth={3} />}</span></div>
            <div className="font-mono text-[10px] text-muted">{f.trainer}</div>
            <div className="font-mono text-[10px] text-dim truncate">{f.shape}</div>
            {!fits && <div className="font-mono text-[9.5px] text-amber">not in project data types</div>}
          </button>
        )
      })}
    </div>
  )
}

export function BundleTree({ slug, formats }: { slug: string; formats: ExportFormat[] }) {
  const stamp = 'YYYYMMDD-HHMMSS'
  return (
    <pre className="font-mono text-[11.5px] leading-[1.6] text-text/85">
      <span className="text-dim">~/.dataset-genie/exports/</span>{slug}/<span className="text-muted">{stamp}</span>/{'\n'}
      {formats.map((f, i) => (
        <span key={f}>{'  '}<span className="text-orange">{f}</span>/{'\n'}{'    '}train.jsonl{'\n'}{'    '}eval.jsonl{i < formats.length - 1 ? '\n' : '\n'}</span>
      ))}
      {'  '}<span className="text-steel">dataset_card.md</span>{'\n'}
      {'  '}<span className="text-steel">generation_config.yaml</span>{'\n'}
      {'  '}<span className="text-steel">manifest.json</span>
    </pre>
  )
}

const LICENSES = ['cc-by-4.0', 'cc-by-sa-4.0', 'cc-by-nc-4.0', 'apache-2.0', 'mit', 'odc-by', 'other']

export function HFPanel({ hf, onChange, status }: { hf: HFPushConfig; onChange: (h: HFPushConfig) => void; status: HFStatus | undefined }) {
  const ok = !!status?.has_token
  return (
    <Panel title="Hugging Face Hub" actions={<Chip tone={ok ? 'ok' : 'amber'}>{ok ? `token · ${status?.username ?? 'ok'}` : 'no token'}</Chip>}>
      <div className="flex flex-col gap-3">
        <Field label="repo" hint="user/name"><Input value={hf.repo_id} onChange={(e) => onChange({ ...hf, repo_id: e.target.value })} placeholder="user/dataset-name" /></Field>
        <div className="flex items-center justify-between"><span className="label">visibility</span><Segmented options={[{ key: 'private', label: 'Private' }, { key: 'public', label: 'Public' }]} value={hf.private ? 'private' : 'public'} onChange={(k) => onChange({ ...hf, private: k === 'private' })} /></div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="licence"><Select value={hf.license} onChange={(e) => onChange({ ...hf, license: e.target.value })}>{LICENSES.map((l) => <option key={l} value={l}>{l}</option>)}</Select></Field>
          <Field label="version tag"><Input value={hf.version_tag} onChange={(e) => onChange({ ...hf, version_tag: e.target.value })} placeholder="v0.1.0" /></Field>
        </div>
        {!ok && <div className="font-mono text-[10.5px] text-amber">Set a Hugging Face token in Settings to enable push.</div>}
      </div>
    </Panel>
  )
}
