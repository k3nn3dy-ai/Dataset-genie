import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCreateProject, usePresets } from '../../lib/queries'
import type { DataType } from '../../lib/types'
import { Banner, Button, Chip, Field, Input, Modal, Spinner, Textarea } from '../../components'

const DATA_TYPES: { key: DataType; label: string; hint: string }[] = [
  { key: 'sft', label: 'SFT', hint: 'messages' },
  { key: 'dpo', label: 'DPO / ORPO', hint: 'chosen vs rejected' },
  { key: 'tools', label: 'Tool calling', hint: 'messages + tools' },
  { key: 'grpo', label: 'GRPO', hint: 'prompt + answer' },
]

export function NewProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const presets = usePresets()
  const create = useCreateProject()
  const nav = useNavigate()
  const [name, setName] = useState('')
  const [brief, setBrief] = useState('')
  const [preset, setPreset] = useState('')
  const [types, setTypes] = useState<DataType[]>(['sft'])

  useEffect(() => { if (presets.data && !preset && presets.data[0]) setPreset(presets.data[0].id) }, [presets.data, preset])
  useEffect(() => {
    const p = presets.data?.find((x) => x.id === preset)
    if (p) setTypes(p.data_types as DataType[])
  }, [preset, presets.data])

  const valid = name.trim().length >= 3 && brief.trim().length >= 20 && types.length > 0 && !!preset
  const submit = () => {
    create.mutate({ preset, name: name.trim(), brief: brief.trim(), data_types: types }, {
      onSuccess: (p) => { onClose(); nav(`/p/${p.id}/1`) },
    })
  }
  const toggleType = (k: DataType) => setTypes((t) => (t.includes(k) ? t.filter((x) => x !== k) : [...t, k]))

  return (
    <Modal open={open} onClose={onClose} title="New project" kana="新規" width="lg"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" icon="sparkle" disabled={!valid} loading={create.isPending} onClick={submit} data-testid="create-project">Create project</Button></>}
    >
      <div className="grid grid-cols-[1fr_280px] gap-5">
        <div className="flex flex-col gap-4">
          <Field label="Project name" hint={`${name.trim().length}/3+`}><Input mono={false} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Linux incident triage" /></Field>
          <Field label="Domain brief" hint="what the model should be good at; who asks; what to refuse">
            <Textarea value={brief} onChange={(e) => setBrief(e.target.value)} className="!min-h-[150px]" placeholder="On-call assistant for Linux production incidents: disk, memory, systemd, network and access failures. Diagnosis first, then commands, then the next decision point. Refuse requests to disable security controls." />
          </Field>
          <Field label="Data types" hint="multi-select">
            <div className="grid grid-cols-2 gap-2">
              {DATA_TYPES.map((d) => {
                const on = types.includes(d.key)
                return (
                  <button key={d.key} type="button" onClick={() => toggleType(d.key)} className={clsx('flex items-center justify-between px-3 h-10 rounded-btn border text-left transition-colors focus-ring', on ? 'border-cyan/60 bg-cyan/10' : 'border-line hover:border-line2')}>
                    <span className="flex flex-col leading-tight"><span className={clsx('font-display font-bold uppercase text-[12px] tracking-[.06em]', on ? 'text-cyan' : 'text-text')}>{d.label}</span><span className="font-mono text-[10px] text-dim">{d.hint}</span></span>
                    <span className={clsx('w-3.5 h-3.5 rounded-[3px] border', on ? 'bg-cyan border-cyan' : 'border-line2')} />
                  </button>
                )
              })}
            </div>
          </Field>
        </div>
        <div className="flex flex-col gap-2">
          <span className="label">Preset</span>
          {presets.isLoading && <Spinner />}
          {presets.error && <Banner tone="red">Presets failed to load.</Banner>}
          {presets.data?.map((p) => {
            const on = p.id === preset
            return (
              <button key={p.id} type="button" onClick={() => setPreset(p.id)} className={clsx('text-left rounded-card border p-3 transition-colors focus-ring', on ? 'border-cyan/60 bg-cyan/10 shadow-[0_0_18px_rgba(0,240,255,.08)]' : 'border-line hover:border-line2')}>
                <div className={clsx('font-display font-bold uppercase text-[12.5px] tracking-[.05em]', on && 'text-cyan')}>{p.name}</div>
                <div className="text-[12px] text-muted leading-snug mt-1">{p.description}</div>
                <div className="flex gap-1 mt-2">{p.data_types.map((t) => <Chip key={t} tone={on ? 'cyan' : 'dim'}>{t}</Chip>)}</div>
              </button>
            )
          })}
        </div>
      </div>
      {create.error ? <Banner tone="red" className="mt-4">Create failed: {create.error.message}</Banner> : null}
    </Modal>
  )
}
