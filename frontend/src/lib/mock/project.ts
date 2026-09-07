import type { Difficulty, ModelSlot, Project, ProjectConfig, TopicNode } from '../types'
import type { Preset } from '../viewtypes'
import { slugify } from '../format'

export const slot = (slug: string, extra: Partial<ModelSlot> = {}): ModelSlot => ({
  slug, provider_order: [], allow_fallbacks: true, temperature: 0.7, max_tokens: 2048, weight: 1, ...extra,
})

export function defaultConfig(): ProjectConfig {
  return {
    data_types: ['sft', 'dpo'], budget_cap_usd: 15, stop_at_pct: 90, concurrency: 8,
    prefer_prompt_caching: true, allow_fallback_providers: true,
    taxonomy: { model: slot('anthropic/claude-sonnet-4', { temperature: 0.4 }), depth: 3, topics: 4, subtopics_per_topic: 2, leaves_per_topic: 4, difficulty_tiers: true, negative_branches: true, rows_per_leaf: 8, task_types: ['TRIAGE', 'EXPLAIN', 'PROCEDURE', 'DECIDE'] },
    prompts: {
      model: slot('openai/gpt-4o-mini', { temperature: 0.9 }),
      personas: [
        { name: 'Junior analyst', style: 'direct, slightly unsure, asks for next steps', weight: 40 },
        { name: 'Senior engineer', style: 'terse, technical, expects precision', weight: 35 },
        { name: 'Manager', style: 'non-technical, wants impact and options', weight: 25 },
      ],
      style_mix: { question: 40, 'paste-log': 25, multipart: 20, 'one-liner': 15 },
      temperature: 0.9, noise_level: 0.15, adversarial_pct: 5, near_dup_threshold: 0.92, embedding_model: 'openai/text-embedding-3-small',
    },
    responses: {
      ensemble: [slot('anthropic/claude-sonnet-4'), slot('openai/gpt-4.1', { weight: 0.5 })], selection: 'weighted', temperature: 0.7, max_tokens: 2048,
      system_prompt: 'You are a precise, helpful Linux incident responder. Give the diagnosis first, then numbered commands, then the next decision point.',
      system_prompt_policy: 'always', system_prompt_random_pct: 50, multi_turn: true,
      simulated_user_model: slot('openai/gpt-4o-mini', { temperature: 0.9 }), turns_min: 2, turns_max: 3, user_mood: 'cooperative', reasoning_tags: false,
    },
    preferences: {
      strategy: 'corruptor', weaker_model: slot('meta-llama/llama-3.1-8b-instruct'), hightemp_temperature: 1.3,
      flaws: [
        { name: 'wrong_fact', weight: 30, instruction: 'Introduce one plausible but incorrect factual claim.' },
        { name: 'skips_next_action', weight: 25, instruction: 'Omit the concrete next action the user needs.' },
        { name: 'over_confident', weight: 20, instruction: 'State uncertain things as certain; remove caveats.' },
        { name: 'dismissive_tone', weight: 15, instruction: 'Adopt a subtly dismissive, condescending tone.' },
        { name: 'hallucinated_tooling', weight: 10, instruction: 'Reference a tool, flag or command that does not exist.' },
      ],
    },
    judge: {
      model: slot('openai/gpt-4o', { temperature: 0 }),
      rubric: [
        { name: 'Correctness', weight: 40, description: 'Facts and commands are accurate.' },
        { name: 'Actionability', weight: 25, description: 'Gives concrete next steps.' },
        { name: 'Style adherence', weight: 20, description: 'Matches the requested style and system prompt.' },
        { name: 'Safety', weight: 15, description: 'Redirects unsafe/out-of-scope requests appropriately.' },
      ],
      low_score_threshold: 3, drop_ties_from_dpo: true,
    },
    filters: { exact_dup: true, near_dup: true, near_dup_threshold: 0.92, refusal: true, pii: true, length: true, min_chars: 40, max_chars: 12000, language: true, expected_language: 'en', embedding_model: 'openai/text-embedding-3-small' },
    export: { formats: ['sft', 'dpo'], eval_split: 0.05, stratify_by: 'leaf', validate_template: 'llama-3.1', include_judge_scores: true, gate_on_score: false, gate_threshold: 3, seed: 42, hf: { repo_id: 'k3nn3dy/linux-incident-triage', private: true, license: 'cc-by-4.0', version_tag: 'v0.1.0' } },
    tools_schemas: [],
  }
}

export const PRESETS: Preset[] = [
  { id: 'support-assistant', name: 'Support assistant', description: 'Customer-facing troubleshooting in a product domain. SFT + DPO, refusal-aware.', data_types: ['sft', 'dpo'] },
  { id: 'coding-agent', name: 'Coding agent', description: 'Tool-calling trajectories over a repo: read, edit, run, verify.', data_types: ['sft', 'tools'] },
  { id: 'domain-expert', name: 'Domain expert', description: 'Deep explanatory answers with cited reasoning in a specialist field.', data_types: ['sft'] },
  { id: 'reasoning-grpo', name: 'Reasoning (GRPO)', description: 'Verifiable-answer problems with <think> traces and an extractable ANSWER line.', data_types: ['grpo'] },
]

// ---------------------------------------------------------------- Linux incident triage taxonomy
type LeafSpec = [label: string, difficulty: Difficulty, task: string]
type TopicSpec = { label: string; subs: { label: string; leaves: LeafSpec[]; negative?: boolean }[] }

const TOPICS: TopicSpec[] = [
  { label: 'Storage & filesystems', subs: [
    { label: 'Disk full', leaves: [['Root filesystem at 100%', 'easy', 'TRIAGE'], ['Deleted-but-open files holding space', 'medium', 'EXPLAIN'], ['Inode exhaustion', 'hard', 'TRIAGE'], ['Log rotation not running', 'easy', 'PROCEDURE']] },
    { label: 'Mounts & I/O', leaves: [['Read-only remount after I/O error', 'hard', 'DECIDE'], ['NFS mount hanging', 'medium', 'TRIAGE'], ['High iowait on database host', 'medium', 'EXPLAIN']] },
  ] },
  { label: 'Memory & processes', subs: [
    { label: 'OOM events', leaves: [['OOM killer terminated a service', 'medium', 'TRIAGE'], ['Memory leak suspected in a Java service', 'hard', 'DECIDE'], ['Swap thrashing', 'easy', 'EXPLAIN']] },
    { label: 'CPU & load', leaves: [['Load average high, CPU idle', 'medium', 'EXPLAIN'], ['Runaway cron job', 'easy', 'PROCEDURE'], ['Zombie processes accumulating', 'easy', 'EXPLAIN']] },
  ] },
  { label: 'Services & systemd', subs: [
    { label: 'Unit failures', leaves: [['Unit failed to start after upgrade', 'medium', 'TRIAGE'], ['Service restart loop', 'medium', 'TRIAGE'], ['Timer not firing', 'easy', 'PROCEDURE']] },
    { label: 'Certificates & time', leaves: [['TLS certificate expired', 'easy', 'PROCEDURE'], ['Clock drift breaking auth', 'hard', 'DECIDE']] },
  ] },
  { label: 'Network & access', subs: [
    { label: 'Connectivity', leaves: [['Host unreachable after firewall change', 'medium', 'TRIAGE'], ['DNS resolution failing intermittently', 'hard', 'TRIAGE'], ['Port in use by unknown process', 'easy', 'PROCEDURE']] },
    { label: 'Authentication', leaves: [['SSH login refused, key rejected', 'easy', 'TRIAGE'], ['Brute-force attempts in auth.log', 'medium', 'DECIDE']] },
    { label: 'Out of scope', negative: true, leaves: [['Requests to disable security controls', 'medium', 'DECIDE'], ['Windows-only troubleshooting', 'easy', 'DECIDE']] },
  ] },
]

export function buildTaxonomy(): TopicNode[] {
  const roots: TopicNode[] = []
  TOPICS.forEach((t, ti) => {
    const tid = `t${ti + 1}`
    const topic: TopicNode = { id: tid, parent_id: null, depth: 0, label: t.label, slug: slugify(t.label), is_negative: false, is_leaf: false, order: ti, children: [] }
    t.subs.forEach((s, si) => {
      const sid = `${tid}-s${si + 1}`
      const sub: TopicNode = { id: sid, parent_id: tid, depth: 1, label: s.label, slug: slugify(s.label), is_negative: !!s.negative, is_leaf: false, order: si, children: [] }
      s.leaves.forEach(([label, difficulty, task_type], li) => {
        sub.children!.push({ id: `${sid}-l${li + 1}`, parent_id: sid, depth: 2, label, slug: slugify(label), difficulty, task_type, is_negative: !!s.negative, is_leaf: true, rows_per_leaf: 8, order: li })
      })
      topic.children!.push(sub)
    })
    roots.push(topic)
  })
  return roots
}

export function flattenLeaves(tree: TopicNode[]): (TopicNode & { path: string[] })[] {
  const out: (TopicNode & { path: string[] })[] = []
  const walk = (n: TopicNode, path: string[]) => {
    const p = [...path, n.label]
    if (n.is_leaf) out.push({ ...n, path: p })
    n.children?.forEach((c) => walk(c, p))
  }
  tree.forEach((n) => walk(n, []))
  return out
}

export function makeProject(id: string, name: string, brief: string, preset: string, cfg: Partial<ProjectConfig> = {}, spend = 0): Project {
  const config = { ...defaultConfig(), ...cfg }
  const now = Date.now() / 1000
  return { id, slug: slugify(name), name, domain_brief: brief, preset, data_types: config.data_types, config, budget_cap_usd: config.budget_cap_usd, stop_at_pct: config.stop_at_pct, spend_usd: spend, created_at: now - 86400 * 2, updated_at: now - 1800 }
}
