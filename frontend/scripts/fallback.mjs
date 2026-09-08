// Regression check: an application-level 502 from the backend (e.g. "bundle built but push failed")
// must surface its `detail` in the UI and must NOT flip the app into mock mode.
// Usage: node scripts/fallback.mjs [baseUrl] [projectId]   (needs the real backend behind baseUrl)
import { chromium } from 'playwright'

const base = process.argv[2] ?? 'http://localhost:5173'
let project = process.argv[3]
if (!project) {
  const list = await (await fetch(`${base}/api/projects/`)).json()
  project = list[0]?.id
  if (!project) { console.error('no projects on the backend'); process.exit(2) }
}
const DETAIL = 'bundle built at /tmp/x but push failed: 403 Forbidden (simulated)'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
await page.route('**/api/projects/*/export', (route) => route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: DETAIL }) }))
await page.goto(`${base}/p/${project}/8?mock=0`, { waitUntil: 'networkidle' })
const btn = page.getByTestId('run-stage') // "Export bundle" — same code path as push, no HF token needed
await btn.waitFor({ state: 'visible' })
if (await btn.isDisabled()) { console.error('export button disabled (project has nothing exportable?)'); await browser.close(); process.exit(2) }
await btn.click()
const banner = page.locator('[role="status"]', { hasText: /export failed/i })
await banner.waitFor({ timeout: 5000 })
const text = await banner.innerText()
const mockBadge = await page.getByText('MOCK', { exact: true }).count()

const failures = []
if (!text.includes(DETAIL)) failures.push(`banner did not show the backend detail; got: ${text}`)
if (/not found/i.test(text)) failures.push(`banner shows the mock-layer error: ${text}`)
if (mockBadge > 0) failures.push('app switched into mock mode on an application-level 502')
await browser.close()
if (failures.length) { console.error('FAIL\n - ' + failures.join('\n - ')); process.exit(1) }
console.log('PASS: 502 detail surfaced, no mock fallback')
