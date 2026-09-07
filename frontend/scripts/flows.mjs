// Interactive flow screenshots: estimate→run, review drawer, new project modal, model picker.
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const base = process.argv[2] ?? 'http://localhost:5173'
mkdirSync('.screens', { recursive: true })
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(`${page.url()} :: ${e.message}`))
page.on('console', (m) => { if (m.type() === 'error') errors.push(`${page.url()} :: console: ${m.text()}`) })

// 1. estimate modal + live run on stage 03
await page.goto(`${base}/p/p_linux/3?mock=1`, { waitUntil: 'networkidle' })
await page.getByTestId('run-stage').click()
await page.waitForTimeout(900)
await page.screenshot({ path: '.screens/flow-estimate.png' })
await page.getByTestId('confirm-run').click()
await page.waitForTimeout(3500)
await page.screenshot({ path: '.screens/flow-running.png' })
await page.getByRole('tab', { name: /raw log/i }).click()
await page.waitForTimeout(400)
await page.screenshot({ path: '.screens/flow-rawlog.png' })
console.log('flow: run ok')

// 2. review drawer
await page.goto(`${base}/p/p_linux/7?mock=1`, { waitUntil: 'networkidle' })
await page.locator('tbody tr').nth(2).click()
await page.waitForTimeout(600)
await page.screenshot({ path: '.screens/flow-review-drawer.png' })
console.log('flow: drawer ok')

// 3. new project modal
await page.goto(`${base}/?mock=1`, { waitUntil: 'networkidle' })
await page.getByTestId('run-stage').click()
await page.waitForTimeout(500)
await page.screenshot({ path: '.screens/flow-new-project.png' })
console.log('flow: modal ok')

// 4. model picker open on settings
await page.goto(`${base}/settings?mock=1`, { waitUntil: 'networkidle' })
await page.locator('button.field').first().click()
await page.waitForTimeout(400)
await page.screenshot({ path: '.screens/flow-model-picker.png' })
console.log('flow: picker ok')

await browser.close()
if (errors.length) { console.log('\nPAGE ERRORS:'); for (const e of errors) console.log(' -', e) }
