import { chromium } from 'playwright'
const base = 'http://127.0.0.1:5173'
const list = await (await fetch(`${base}/api/projects/`)).json()
const browser = await chromium.launch(); const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []; page.on('pageerror', (e) => errors.push(e.message))
await page.goto(`${base}/p/${list[0].id}/1?mock=0`, { waitUntil: 'networkidle' })
await page.getByTestId('run-stage').click(); await page.waitForTimeout(1200)
await page.screenshot({ path: '.screens-real/flow-estimate.png' })
await page.getByTestId('confirm-run').click(); await page.waitForTimeout(1200)
await page.screenshot({ path: '.screens-real/flow-run-error.png' })
await page.goto(`${base}/settings?mock=0`, { waitUntil: 'networkidle' }); await page.waitForTimeout(600)
await page.screenshot({ path: '.screens-real/settings.png' })
await browser.close(); console.log('done', errors)
