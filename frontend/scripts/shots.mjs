// Screenshot every route with mock data. Usage: node scripts/shots.mjs [baseUrl] [route ...]
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const base = process.argv[2] ?? 'http://localhost:5173'
const only = process.argv.slice(3)
const PROJECT = 'p_linux'
const routes = [
  ['projects', '/'],
  ['kit', '/_kit'],
  ['01-taxonomy', `/p/${PROJECT}/1`],
  ['02-prompts', `/p/${PROJECT}/2`],
  ['03-responses', `/p/${PROJECT}/3`],
  ['04-rejected', `/p/${PROJECT}/4`],
  ['05-judge', `/p/${PROJECT}/5`],
  ['06-filter', `/p/${PROJECT}/6`],
  ['07-review', `/p/${PROJECT}/7`],
  ['08-export', `/p/${PROJECT}/8`],
  ['settings', '/settings'],
  ['empty-project', '/p/p_grafana/3'],
]
mkdirSync('.screens', { recursive: true })
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
const errors = []
page.on('pageerror', (e) => errors.push(`${page.url()} :: ${e.message}`))
page.on('console', (m) => { if (m.type() === 'error') errors.push(`${page.url()} :: console: ${m.text()}`) })
for (const [name, path] of routes) {
  if (only.length && !only.includes(name)) continue
  await page.goto(`${base}${path}${path.includes('?') ? '&' : '?'}mock=1`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  await page.screenshot({ path: `.screens/${name}.png`, fullPage: false })
  console.log('shot', name)
}
await browser.close()
if (errors.length) { console.log('\nPAGE ERRORS:'); for (const e of errors) console.log(' -', e) }
