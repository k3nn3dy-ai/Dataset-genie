import { useEffect, useState } from 'react'
import { Header } from '../../app/Header'
import { KANA } from '../../lib/types'
import type { SettingsData } from '../../lib/viewtypes'
import { usePutSettings, useRefreshModels, useSettings } from '../../lib/queries'
import { Banner, Button, ErrorState, Field, ModelPicker, NumberInput, Panel, Select, Slider, Spinner, Toggle } from '../../components'
import { SecretRow } from './SecretRow'

const STAGE_SLOTS: { key: keyof SettingsData['default_models']; label: string; kana: string }[] = [
  { key: 'taxonomy', label: 'Stage 01 · Taxonomy', kana: '分類' }, { key: 'prompts', label: 'Stage 02 · Prompts', kana: 'プロンプト' }, { key: 'responses', label: 'Stage 03 · Teacher', kana: '応答' },
  { key: 'judge', label: 'Stage 05 · Judge', kana: '審査' }, { key: 'simulated_user', label: 'Simulated user', kana: '対話' },
]
const PROVIDERS = ['', 'anthropic', 'openai', 'google', 'together', 'fireworks', 'deepinfra', 'groq']

export function SettingsScreen() {
  const settings = useSettings()
  const put = usePutSettings()
  const refresh = useRefreshModels()
  const [draft, setDraft] = useState<SettingsData | null>(null)
  useEffect(() => { if (settings.data && !draft) setDraft(settings.data) }, [settings.data, draft])
  const dirty = !!draft && !!settings.data && JSON.stringify(draft) !== JSON.stringify(settings.data)
  const set = (p: Partial<SettingsData>) => setDraft((d) => (d ? { ...d, ...p } : d))

  return (
    <>
      <Header kicker="SYSTEM · KEYS, MODELS, LIMITS" kana={KANA.settings} title="Settings"
        subtitle="Secrets live in the macOS keychain, never in the database, YAML or dataset cards."
        actions={<Button variant="primary" size="lg" icon="check" disabled={!dirty} loading={put.isPending} onClick={() => draft && put.mutate(draft)} data-testid="run-stage">Save settings</Button>} />
      {settings.error && <ErrorState error={settings.error} onRetry={() => settings.refetch()} />}
      {settings.isLoading && <Spinner />}
      {draft && (
        <div className="grid grid-cols-[400px_minmax(0,1fr)] gap-5 items-start">
          <div className="flex flex-col gap-4">
            <Panel title="Secrets" kana="秘密">
              <div className="flex flex-col gap-4">
                <SecretRow name="openrouter" label="OpenRouter API key" hint="sk-or-v1-…" />
                <SecretRow name="huggingface" label="Hugging Face token" hint="hf_…" />
                <Banner tone="cyan" icon="lock">Stored in the macOS keychain under service <span className="font-mono">dataset-genie</span>. The UI only ever sees set / unset.</Banner>
              </div>
            </Panel>
            <Panel title="Budget defaults" kana="予算">
              <div className="flex flex-col gap-4">
                <Field label="cap per project" hint="USD"><NumberInput value={draft.budget_cap_usd} min={1} max={1000} step={1} unit="USD" onChange={(budget_cap_usd) => set({ budget_cap_usd })} /></Field>
                <Slider label="auto-stop at" value={draft.stop_at_pct} min={50} max={100} format={(v) => `${v}% of cap`} tone="amber" onChange={(stop_at_pct) => set({ stop_at_pct })} />
                <Slider label="concurrency" value={draft.concurrency} min={1} max={32} format={(v) => `${v} workers`} onChange={(concurrency) => set({ concurrency })} />
              </div>
            </Panel>
            <Panel title="Routing" kana="経路">
              <div className="flex flex-col gap-4">
                <Toggle checked={draft.prefer_prompt_caching} onChange={(prefer_prompt_caching) => set({ prefer_prompt_caching })} label="Prefer prompt-caching providers" hint="Route to providers that cache the system prompt" />
                <Toggle checked={draft.allow_fallback_providers} onChange={(allow_fallback_providers) => set({ allow_fallback_providers })} label="Allow fallback providers" hint="Let OpenRouter re-route on provider errors" />
                <Field label="pin provider" hint="overrides fallbacks">
                  <Select value={draft.pinned_provider} onChange={(e) => set({ pinned_provider: e.target.value })}>{PROVIDERS.map((p) => <option key={p} value={p}>{p || 'none (auto)'}</option>)}</Select>
                </Field>
              </div>
            </Panel>
          </div>
          <Panel title="Default model per stage" kana="既定" actions={<Button size="sm" variant="outline" icon="refresh" loading={refresh.isPending} onClick={() => refresh.mutate()}>Refresh catalogue</Button>}>
            <div className="grid grid-cols-2 gap-4">
              {STAGE_SLOTS.map((s) => (
                <div key={s.key} className="rounded-card border border-line bg-bg/40 p-3">
                  <ModelPicker label={`${s.label} · ${s.kana}`} value={draft.default_models[s.key]} onChange={(m) => set({ default_models: { ...draft.default_models, [s.key]: m } })} />
                </div>
              ))}
            </div>
            <p className="font-mono text-[10.5px] text-dim mt-4">Defaults seed new projects; each project can override every slot. Prices shown are OpenRouter list prices per 1M tokens.</p>
          </Panel>
        </div>
      )}
      {put.error ? <Banner tone="red" className="mt-4">Save failed: {put.error.message}</Banner> : null}
    </>
  )
}
