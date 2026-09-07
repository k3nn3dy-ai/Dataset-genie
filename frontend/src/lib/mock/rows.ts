import type { Message, Pair, RowStatus, TopicNode } from '../types'
import type { PromptItem, RowItem } from '../viewtypes'
import { mulberry32, pick } from '../rng'
import { flattenLeaves } from './project'

// Domain snippets so the demo reads like real Linux incident triage, not lorem ipsum.
const KNOW: Record<string, { symptom: string; cmds: string[]; cause: string; next: string }> = {
  'root-filesystem-at-100': { symptom: 'writes failing with ENOSPC on /', cmds: ['df -hT /', 'du -xh --max-depth=1 / | sort -h | tail', 'journalctl --disk-usage'], cause: 'journal and /var/log growth after a logging misconfiguration', next: 'vacuum the journal (`journalctl --vacuum-size=500M`) and cap it in journald.conf' },
  'deleted-but-open-files-holding-space': { symptom: 'df shows full but du shows far less', cmds: ['lsof +L1 | head', 'ls -l /proc/*/fd 2>/dev/null | grep deleted'], cause: 'a process still holds a deleted log open, so the blocks are not released', next: 'truncate via `: > /proc/<pid>/fd/<n>` or restart the owning service' },
  'inode-exhaustion': { symptom: 'ENOSPC while df -h shows free space', cmds: ['df -i', 'find / -xdev -printf "%h\\n" | sort | uniq -c | sort -rn | head'], cause: 'millions of tiny session files under /var/lib/php/sessions', next: 'purge stale sessions and add a systemd timer to keep them pruned' },
  'oom-killer-terminated-a-service': { symptom: 'service died with no error in its own log', cmds: ['journalctl -k | grep -i "out of memory"', 'dmesg -T | grep -i oom', 'systemctl status api.service'], cause: 'the kernel OOM killer chose the process with the highest oom_score', next: 'set MemoryMax in the unit and inspect the heap growth before raising limits' },
  'ssh-login-refused-key-rejected': { symptom: 'Permission denied (publickey)', cmds: ['ssh -vvv user@host', 'ls -ld ~ ~/.ssh ~/.ssh/authorized_keys', 'journalctl -u sshd -n 50'], cause: 'authorized_keys is group-writable so sshd ignores it (StrictModes)', next: 'chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys' },
  'tls-certificate-expired': { symptom: 'clients failing with certificate has expired', cmds: ['openssl s_client -connect host:443 </dev/null 2>/dev/null | openssl x509 -noout -dates', 'systemctl list-timers | grep certbot'], cause: 'the renewal timer was disabled during a package upgrade', next: 'renew now (`certbot renew --force-renewal`) and re-enable the timer' },
  'unit-failed-to-start-after-upgrade': { symptom: 'systemctl start exits 1 immediately', cmds: ['systemctl status nginx --no-pager -l', 'journalctl -xeu nginx', 'nginx -t'], cause: 'a deprecated directive was removed in the new version', next: 'fix the config, run `nginx -t`, then `systemctl restart nginx`' },
  'dns-resolution-failing-intermittently': { symptom: 'roughly one in ten lookups times out', cmds: ['resolvectl status', 'dig +short example.internal @10.0.0.53', 'ss -uap | grep :53'], cause: 'one of two upstream resolvers is dropping UDP', next: 'remove the bad upstream from resolved.conf and confirm with a `dig` loop' },
}
const GENERIC = { symptom: 'the service is degraded and the on-call was paged', cmds: ['systemctl --failed', 'journalctl -p err -b', 'top -b -n1 | head -20'], cause: 'a recent change interacting with an existing limit', next: 'confirm the hypothesis with the commands above before changing anything' }

const STYLES = ['question', 'paste-log', 'multipart', 'one-liner'] as const
const PERSONAS = ['Junior analyst', 'Senior engineer', 'Manager'] as const
const TEACHERS = ['anthropic/claude-sonnet-4', 'openai/gpt-4.1']
const FLAWS = ['wrong_fact', 'skips_next_action', 'over_confident', 'dismissive_tone', 'hallucinated_tooling']

function promptText(style: string, leaf: string, k: typeof GENERIC, rnd: () => number): string {
  const host = pick(rnd, ['web-03', 'db-primary', 'batch-07', 'edge-gw-1', 'ci-runner-12'])
  switch (style) {
    case 'paste-log': return `Seeing this on ${host} and not sure what to do:\n\n[${new Date().toISOString().slice(0, 10)} 03:${Math.floor(rnd() * 60).toString().padStart(2, '0')}:14] ${host} kernel: ${k.symptom}\n\nIt's ${leaf.toLowerCase()}, right? What's the fastest safe fix?`
    case 'multipart': return `${leaf} on ${host}. Three things: (1) how do I confirm it's actually that, (2) what's the least risky immediate fix, (3) what should I change so it doesn't recur?`
    case 'one-liner': return `${host}: ${k.symptom}. fix?`
    default: return `We think we're hitting "${leaf.toLowerCase()}" on ${host} — ${k.symptom}. How should I triage this before I start restarting things?`
  }
}

function answerText(leaf: string, k: typeof GENERIC): string {
  return `**Diagnosis:** this matches ${leaf.toLowerCase()} — the tell is that ${k.symptom}. Most often the cause is ${k.cause}.\n\n**Confirm it first:**\n${k.cmds.map((c, i) => `${i + 1}. \`${c}\``).join('\n')}\n\n**If confirmed:** ${k.next}.\n\n**Decision point:** if the numbers above do not support this diagnosis, stop and capture the outputs before restarting anything — a restart can destroy the evidence you need.`
}

function corrupt(answer: string, flaw: string): string {
  switch (flaw) {
    case 'wrong_fact': return answer.replace('kernel OOM killer', 'systemd watchdog').replace('StrictModes', 'PermitRootLogin').replace('ENOSPC', 'EACCES')
    case 'skips_next_action': return answer.replace(/\n\n\*\*If confirmed:\*\*[^\n]*/, '')
    case 'over_confident': return answer.replace('Most often the cause is', 'The cause is definitely').replace(/\n\n\*\*Decision point:\*\*[\s\S]*$/, '\n\nNo need to check anything else.')
    case 'dismissive_tone': return answer.replace('**Diagnosis:**', '**Diagnosis:** (this is fairly basic, but)').replace('**Confirm it first:**', '**If you insist on checking:**')
    default: return answer.replace(k1(answer), '`sysfixd --auto-repair`')
  }
}
function k1(s: string): string { const m = s.match(/`[^`]+`/); return m ? m[0] : '' }

export interface Generated { prompts: PromptItem[]; rows: RowItem[]; pairs: Pair[] }

export function generate(tree: TopicNode[], slug: string, seed = 7): Generated {
  const rnd = mulberry32(seed)
  const leaves = flattenLeaves(tree)
  const prompts: PromptItem[] = []
  const rows: RowItem[] = []
  const pairs: Pair[] = []
  const rowsPerLeaf = 5
  for (const leaf of leaves) {
    const k = KNOW[leaf.slug] ?? GENERIC
    for (let n = 1; n <= rowsPerLeaf; n++) {
      const style = pick(rnd, STYLES)
      const persona = pick(rnd, PERSONAS)
      const adversarial = rnd() < 0.05
      const id = `${slug}-${leaf.slug}-${String(n).padStart(4, '0')}`
      const text = promptText(style, leaf.label, k, rnd)
      prompts.push({ id, leaf_id: leaf.id, leaf_path: leaf.path, text, persona, style, difficulty: leaf.difficulty ?? 'medium', adversarial })
      const teacher = TEACHERS[n % TEACHERS.length]
      const isRefusal = leaf.is_negative ? rnd() < 0.6 : rnd() < 0.03
      const answer = isRefusal
        ? `I can't help with ${leaf.label.toLowerCase()} in that form. If the goal is to keep the service available, I can walk through the supported options instead.`
        : answerText(leaf.label, k)
      const messages: Message[] = [
        { role: 'system', content: 'You are a precise, helpful Linux incident responder.' },
        { role: 'user', content: text },
        { role: 'assistant', content: answer },
      ]
      if (!isRefusal && rnd() < 0.35) {
        messages.push({ role: 'user', content: 'Ran the first two. Output is below — what now?\n\n' + k.cmds[0] + '\n... 98% /' })
        messages.push({ role: 'assistant', content: `That confirms it. Do this now: ${k.next}. Then re-run \`${k.cmds[0]}\` and watch the number move before you close the incident.` })
      }
      const scoreBase = isRefusal ? 2.2 : 3.4 + rnd() * 1.6 - (leaf.difficulty === 'hard' ? 0.3 : 0)
      const score = Math.round(Math.max(0.5, Math.min(5, scoreBase + (rnd() - 0.5) * 0.6)) * 10) / 10
      const flags: string[] = []
      if (isRefusal) flags.push('refusal')
      if (score < 3) flags.push('low_score')
      if (rnd() < 0.04) flags.push('pii')
      if (rnd() < 0.05) flags.push('near_dup')
      let status: RowStatus = 'accepted'
      if (isRefusal) status = 'refusal'
      else if (flags.includes('pii') || flags.includes('near_dup')) status = 'filtered'
      else if (rnd() < 0.08) status = 'flagged'
      else if (rnd() < 0.06) status = 'edited'
      const filter_reason = status === 'filtered' ? (flags.includes('pii') ? 'pii: non-RFC1918 IPv4 in assistant turn' : 'near_dup: cosine 0.94 vs ' + `${slug}-${leaf.slug}-0001`) : null
      const criteria = { Correctness: Math.round(score), Actionability: Math.min(5, Math.round(score + 0.4)), 'Style adherence': Math.max(1, Math.round(score - 0.3)), Safety: isRefusal ? 5 : 4 }
      rows.push({
        messages, tools: null, status, filter_reason,
        metadata: { id, leaf_id: leaf.id, leaf_path: leaf.path, difficulty: leaf.difficulty ?? 'medium', task_type: leaf.task_type ?? 'TRIAGE', persona, style, adversarial, models: { prompts: 'openai/gpt-4o-mini', responses: teacher, judge: 'openai/gpt-4o' }, judge: { score, criteria, rationale: isRefusal ? 'Correct refusal for a negative-branch prompt, but offers no redirect detail.' : score >= 4 ? 'Accurate commands, clear ordering, explicit decision point before destructive action.' : 'Commands are right but the follow-up omits verification; tone drifts from the system prompt.' }, flags },
      })
      if (status === 'accepted' && !isRefusal) {
        const flaw = pick(rnd, FLAWS)
        const last = messages[messages.length - 1]
        const rejectedText = corrupt(last.content ?? '', flaw)
        const tie = rnd() < 0.06
        pairs.push({ prompt: messages.slice(0, -1), chosen: [last], rejected: [{ role: 'assistant', content: rejectedText }], metadata: { ...rows[rows.length - 1].metadata, strategy: 'corruptor', flaw, judge: { score, criteria, rationale: tie ? 'Both responses reach the same fix; the injected flaw is cosmetic.' : `Rejected response ${flaw.replace('_', ' ')}; chosen keeps the verification step.`, verdict: tie ? 'tie' : 'chosen' } } })
      }
    }
  }
  return { prompts, rows, pairs }
}
