import { chromium } from 'playwright'
const base = process.argv[2] ?? 'http://127.0.0.1:5173'
const browser = await chromium.launch(); const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []; page.on('pageerror', (e) => errors.push(e.message))
await page.goto(`${base}/p/p_k8s/3?mock=1`, { waitUntil: 'networkidle' })
await page.getByTestId('resume-run').click(); await page.waitForTimeout(2500)
await page.screenshot({ path: '.screens/flow-resumed.png' })
await browser.close(); console.log('resume flow done', errors)
