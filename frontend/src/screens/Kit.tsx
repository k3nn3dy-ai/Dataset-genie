import { useState } from 'react'
import { Header } from '../app/Header'
import { BudgetBar } from '../app/BudgetBar'
import { Banner, Button, Chip, EmptyState, ErrorState, Field, GhostNumeral, Histogram, ICON_NAMES, Icon, IconButton, Input, Kicker, Label, Modal, ModelPicker, MonoTable, NumberInput, Panel, RadioGroup, Segmented, Select, Slider, Spinner, StatTile, Tabs, Textarea, Toggle } from '../components'
import type { ModelSlot } from '../lib/types'

const ROWS = Array.from({ length: 6 }, (_, i) => ({ id: `linux-incident-triage-oom-${String(i + 1).padStart(4, '0')}`, leaf: 'OOM killer', score: 4.6 - i * 0.4, flags: i % 3 === 0 ? ['low_score'] : [] }))

export function Kit() {
  const [tab, setTab] = useState('a')
  const [on, setOn] = useState(true)
  const [v, setV] = useState(35)
  const [modal, setModal] = useState(false)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [seg, setSeg] = useState<'private' | 'public'>('private')
  const [radio, setRadio] = useState<'corruptor' | 'weaker'>('corruptor')
  const [slot, setSlot] = useState<ModelSlot>({ slug: 'anthropic/claude-sonnet-4', provider_order: [], allow_fallbacks: true, temperature: 0.7, max_tokens: 2048, weight: 1 })
  return (
    <div className="flex flex-col gap-6">
      <Header kicker="COMPONENT KIT · EVERY PRIMITIVE" kana="部品" title="Kit" numeral="00" subtitle="Every primitive, rendered once. The RUN STAGE primary lives in the Buttons panel below." />
      <Panel title="Buttons" kana="ボタン">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" icon="play">Run stage</Button><Button variant="magenta" icon="sparkle">Magenta</Button><Button variant="outline" icon="export">Outline</Button><Button variant="ghost" icon="refresh">Ghost</Button><Button variant="danger" icon="trash">Danger</Button>
          <Button size="sm" variant="primary">Small</Button><Button size="lg" variant="primary" icon="play">Large</Button><Button loading variant="primary">Loading</Button><Button disabled variant="primary">Disabled</Button>
          <IconButton icon="settings" label="Settings" /><IconButton icon="x" label="Close" size="sm" />
        </div>
      </Panel>
      <div className="grid grid-cols-4 gap-3">
        <StatTile label="rows accepted" value="1,024" delta="+12%" deltaTone="acid" tone="cyan" />
        <StatTile label="refusal rate" value="3.2" unit="%" tone="amber" hint="12 of 380" />
        <StatTile label="spend" value="$6.42" tone="magenta" delta="43% cap" />
        <StatTile label="errors" value="0" tone="acid" />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Panel title="Tabs · Toggle · Slider · Chips">
          <Tabs value={tab} onChange={setTab} tabs={[{ key: 'a', label: 'Results', count: 42 }, { key: 'b', label: 'Raw log' }, { key: 'c', label: 'Refusals', count: 3 }]} />
          <div className="mt-4 flex flex-col gap-4">
            <Toggle checked={on} onChange={setOn} label="Gate export on score" hint="Scores are visible, never gating by default" />
            <Toggle checked={!on} onChange={(x) => setOn(!x)} label="Acid toggle" tone="acid" size="sm" />
            <Slider label="adversarial" value={v} onChange={setV} format={(x) => `${x}%`} />
            <Slider label="temperature" value={0.7} onChange={() => undefined} min={0} max={2} step={0.05} tone="magenta" format={(x) => x.toFixed(2)} />
            <div className="flex flex-wrap gap-1.5">
              <Chip>default</Chip><Chip tone="cyan">edited</Chip><Chip tone="magenta">near_dup</Chip><Chip tone="acid">accepted</Chip><Chip tone="amber">refusal</Chip><Chip tone="red">low_score</Chip><Chip tone="dim">todo</Chip><Chip tone="cyan" onRemove={() => undefined}>removable</Chip><Chip tone="default" onClick={() => undefined} active>clickable</Chip>
            </div>
            <div className="flex gap-3 items-center"><Segmented options={[{ key: 'private', label: 'Private' }, { key: 'public', label: 'Public' }]} value={seg} onChange={setSeg} /><Spinner /></div>
            <RadioGroup value={radio} onChange={setRadio} options={[{ key: 'corruptor', label: 'Corruptor', hint: 'Same teacher injects one flaw' }, { key: 'weaker', label: 'Weaker model', hint: 'A cheaper model answers' }]} />
          </div>
        </Panel>
        <Panel title="Fields · ModelPicker">
          <div className="flex flex-col gap-3">
            <Field label="Project name" hint="required"><Input placeholder="Linux incident triage" mono={false} /></Field>
            <Field label="Repo"><Input placeholder="user/name" /></Field>
            <Field label="Brief"><Textarea placeholder="Describe the domain…" /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Stratify"><Select defaultValue="leaf"><option value="leaf">leaf</option><option value="topic">topic</option></Select></Field>
              <Field label="Cap"><NumberInput value={15} onChange={() => undefined} unit="USD" /></Field>
            </div>
            <ModelPicker label="Judge model" value={slot} onChange={setSlot} warn="Same family as teacher" />
            <Label hint="hint">Label with hint</Label>
            <Kicker text="STAGE 03 · THE TEACHER ANSWERS" kana="応答" />
            <div className="flex items-center gap-6"><GhostNumeral value="03" size={72} /><BudgetBar spend={4.2} cap={15} className="flex-1" /><BudgetBar spend={12} cap={15} className="flex-1" /><BudgetBar spend={14.1} cap={15} className="flex-1" /></div>
          </div>
        </Panel>
      </div>
      <Panel title="MonoTable" actions={<Button size="sm" variant="outline" icon="filter">Filter</Button>}>
        <MonoTable rows={ROWS} rowKey={(r) => r.id} selectable selected={sel} onSelectedChange={setSel} onRowClick={() => undefined} activeKey={ROWS[1].id}
          columns={[
            { key: 'id', header: 'id', render: (r) => <span className="text-cyan">{r.id}</span>, sortValue: (r) => r.id },
            { key: 'leaf', header: 'leaf', render: (r) => r.leaf },
            { key: 'score', header: 'score', align: 'right', render: (r) => r.score.toFixed(1), sortValue: (r) => r.score },
            { key: 'flags', header: 'flags', render: (r) => <div className="flex gap-1">{r.flags.map((f) => <Chip key={f} tone="red">{f}</Chip>)}</div> },
          ]} />
      </Panel>
      <div className="grid grid-cols-2 gap-4">
        <Panel title="Histogram"><Histogram bins={[1, 2, 4, 6, 9, 14, 22, 31, 18, 7]} threshold={3} mean={3.72} /></Panel>
        <Panel title="Icons">
          <div className="grid grid-cols-8 gap-2">
            {ICON_NAMES.map((n) => <div key={n} className="flex flex-col items-center gap-1 py-2 rounded-btn border border-line text-muted"><Icon name={n} size={16} className="text-cyan" /><span className="font-mono text-[9px]">{n}</span></div>)}
          </div>
        </Panel>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <EmptyState title="No project selected" kana="空" body="Pick a project from the list, or create one to start the pipeline." action={{ label: 'New project', onClick: () => setModal(true), icon: 'plus' }} />
        <ErrorState error={new Error('ApiError 500: taxonomy generation failed — provider timeout after 60s')} onRetry={() => undefined} />
      </div>
      <div className="flex flex-col gap-2"><Banner tone="amber">Judge model shares a family with the teacher.</Banner><Banner tone="cyan" icon="lock">Cap is enforced server-side.</Banner><Banner tone="red">Export aborted: validation failed.</Banner><Banner tone="acid" icon="check">Pushed to Hub.</Banner></div>
      <Modal open={modal} onClose={() => setModal(false)} title="Modal" kana="窓" footer={<><Button onClick={() => setModal(false)}>Cancel</Button><Button variant="primary" icon="check" onClick={() => setModal(false)}>Confirm</Button></>}>
        <p className="text-muted">Modal body content.</p>
      </Modal>
    </div>
  )
}
