// Small formatting helpers used across screens. Pure functions; unit-testable.
export function usd(v: number, digits = 2): string {
  return `$${v.toFixed(digits)}`
}

export function usdCompact(v: number): string {
  if (v < 0.01 && v > 0) return `$${v.toFixed(4)}`
  return usd(v)
}

export function pct(v: number, digits = 0): string {
  return `${v.toFixed(digits)}%`
}

export function num(v: number): string {
  return v.toLocaleString('en-US')
}

export function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

export function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}

export function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
}

export function relTime(ts: number): string {
  const d = Date.now() / 1000 - ts
  if (d < 60) return 'just now'
  if (d < 3600) return `${Math.floor(d / 60)}m ago`
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`
  return `${Math.floor(d / 86400)}d ago`
}

export function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

export function contextK(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}k`
}

/** Model family = slug prefix before '/', with a small alias map (mirrors backend). */
const FAMILY_ALIASES: Record<string, string> = { 'x-ai': 'xai', 'meta-llama': 'meta' }
export function modelFamily(slug: string): string {
  const prefix = slug.split('/')[0] ?? slug
  return FAMILY_ALIASES[prefix] ?? prefix
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}
