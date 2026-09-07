// Paused-banner + conflict flows (mock). Usage: node scripts/resume.mjs [baseUrl]
import { chromium } from 'playwright'
const base = process.argv[2] ?? 'http://127.0.0.1:5173'
const browser = await chromium.launch(); const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []; page.on('pageerror', (e) => errors.push(e.message))
// paused banner with partial item, then force-resume
await page.goto(`${base}/p/p_k8s/3?mock=1`, { waitUntil: 'networkidle' }); await page.waitForTimeout(500)
await page.screenshot({ path: '.screens/paused-stage.png' })
await page.getByTestId('resume-run-force').click(); await page.waitForTimeout(2500)
await page.screenshot({ path: '.screens/flow-resumed.png' })
// while that run is live, start another stage on the same project → 409 run_conflict
await page.goto(`${base}/p/p_k8s/4?mock=1`, { waitUntil: 'networkidle' })
await page.getByTestId('run-stage').click(); await page.waitForTimeout(900)
await page.getByTestId('confirm-run').click(); await page.waitForTimeout(700)
await page.screenshot({ path: '.screens/flow-run-conflict.png' })
await page.getByTestId('open-running-stage').click(); await page.waitForTimeout(800)
const url = page.url()
await browser.close(); console.log('flows done', { url, errors })
