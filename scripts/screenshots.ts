/**
 * Playwright screenshot pass over every Dataset Genie screen → docs/screenshots/*.png
 *
 *   cd frontend && npx tsx ../scripts/screenshots.ts        # or `make screenshots` from the repo root
 *
 * Env:
 *   BASE_URL   default http://localhost:5173 (vite dev server; /api proxied to :8765)
 *   PROJECT    project id or slug to shoot (default: the seeded "linux-incident-triage")
 *   MOCK=1     force `?mock=1` (frontend mock layer) even if the backend answers
 *   OUT_DIR    default <repo>/docs/screenshots
 *   ONLY       comma-separated file stems to (re)shoot, e.g. ONLY=08-review,kit
 *
 * If the backend is unreachable the script falls back to `?mock=1` automatically.
 */
import { mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from 'playwright'

const HERE = dirname(fileURLToPath(import.meta.url))
// Resolve playwright from frontend/node_modules regardless of where this script lives.
const { chromium } = createRequire(resolve(HERE, '..', 'frontend', 'package.json'))('playwright') as typeof import('playwright')
const BASE_URL = (process.env.BASE_URL ?? 'http://localhost:5173').replace(/\/$/, '')
const OUT_DIR = process.env.OUT_DIR ?? resolve(HERE, '..', 'docs', 'screenshots')
const WANT_PROJECT = process.env.PROJECT ?? 'linux-incident-triage'
const FORCE_MOCK = process.env.MOCK === '1'
const WIDTH = 1600
const HEIGHT = 1000
const ONLY = new Set((process.env.ONLY ?? '').split(',').map((s) => s.trim()).filter(Boolean))
const SETTLE_MS = Number(process.env.SETTLE_MS ?? 900) // let the lattice / glitch / rain reach a steady frame

const STAGE_FILES: Record<number, string> = {
  1: '02-taxonomy', 2: '03-prompts', 3: '04-responses', 4: '05-rejected',
  5: '06-judge', 6: '07-filter', 7: '08-review', 8: '09-export',
}

type ProjectLite = { id: string; slug: string; name: string }

async function resolveProject(): Promise<{ id: string; mock: boolean }> {
  if (FORCE_MOCK) return { id: 'demo', mock: true }
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 3000)
    const res = await fetch(`${BASE_URL}/api/projects/`, { signal: ctrl.signal })
    clearTimeout(t)
    if (!res.ok) throw new Error(`GET /api/projects → ${res.status}`)
    const body = (await res.json()) as ProjectLite[] | { items: ProjectLite[] }
    const list = Array.isArray(body) ? body : body.items
    const hit = list.find((p) => p.id === WANT_PROJECT || p.slug === WANT_PROJECT) ?? list[0]
    if (!hit) throw new Error('no projects in backend — run `make seed-demo` first')
    console.log(`backend up; shooting project "${hit.name}" (${hit.id})`)
    return { id: hit.id, mock: false }
  } catch (e) {
    console.warn(`backend unavailable (${(e as Error).message}); falling back to ?mock=1`)
    return { id: 'demo', mock: true }
  }
}

async function settle(page: Page) {
  // screens poll /summary, so the network may never go idle: bound the wait
  await page.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {})
  await Promise.race([
    page.evaluate(() => (document as Document & { fonts: FontFaceSet }).fonts.ready),
    new Promise((r) => setTimeout(r, 5000)),
  ])
  // wait until every web font we rely on is actually loaded (not just the API resolved)
  await page.waitForFunction(() => {
    const fs = (document as Document & { fonts: FontFaceSet }).fonts
    // check the weights actually loaded from Google Fonts (700 / 500 / 400)
    const want = ['bold 16px "Chakra Petch"', '500 16px "Rajdhani"', '16px "Share Tech Mono"']
    return want.every((f) => fs.check(f))
  }, undefined, { timeout: 8000 }).catch(() => console.warn('  (web fonts did not report ready; continuing)'))
  await page.waitForTimeout(SETTLE_MS)
}

async function shoot(page: Page, path: string, file: string, mock: boolean) {
  if (ONLY.size && !ONLY.has(file)) return
  const url = `${BASE_URL}${path}${mock ? (path.includes('?') ? '&' : '?') + 'mock=1' : ''}`
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 })
  await settle(page)
  const out = resolve(OUT_DIR, `${file}.png`)
  await page.screenshot({ path: out, fullPage: false })
  const title = await page.title()
  const h1 = (await page.locator('h1').first().textContent({ timeout: 1500 }).catch(() => null))?.trim() ?? '(no h1)'
  console.log(`  ${file}.png  ←  ${path}   [${title} · ${h1}]`)
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true })
  const { id, mock } = await resolveProject()
  const browser = await chromium.launch()
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    reducedMotion: 'no-preference',
  })
  const page = await context.newPage()
  const consoleErrors: string[] = []
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`))
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })

  await shoot(page, '/', '01-projects', mock)
  for (let n = 1; n <= 8; n++) await shoot(page, `/p/${id}/${n}`, STAGE_FILES[n], mock)
  await shoot(page, '/settings', '10-settings', mock)
  await shoot(page, '/_kit', 'kit', mock)

  await browser.close()
  if (consoleErrors.length) {
    console.warn(`\n${consoleErrors.length} console error(s) during capture:`)
    for (const e of [...new Set(consoleErrors)].slice(0, 20)) console.warn('  - ' + e.slice(0, 300))
  }
  console.log(`\nwrote ${ONLY.size || 11} screenshots to ${OUT_DIR}`)
}

main().catch((e) => { console.error(e); process.exit(1) })
