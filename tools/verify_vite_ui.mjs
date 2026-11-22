import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../web-legacy/package.json', import.meta.url))
const { chromium } = require('playwright')

const baseUrl = process.env.WEB_BASE_URL || 'http://127.0.0.1:3100'
const email = process.env.WORKFLOW_ADMIN_EMAIL || 'admin@example.com'
const password = process.env.WORKFLOW_ADMIN_PASSWORD || 'Password123'
const screenshots = new URL('../.data/acceptance/', import.meta.url)
await mkdir(screenshots, { recursive: true })

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || undefined,
})
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
const failedResponses = []
page.on('pageerror', (error) => errors.push(error.stack || error.message))
page.on('console', (message) => {
  if (message.type() !== 'error') return
  void Promise.all(message.args().map(async (argument) => {
    try {
      return await argument.evaluate((value) => value instanceof Error ? value.stack : String(value))
    } catch {
      return ''
    }
  })).then((details) => errors.push(`${message.text()} ${details.join(' ')} ${JSON.stringify(message.location())}`))
})
page.on('response', (response) => {
  if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`)
})

try {
  await page.goto(`${baseUrl}/signin`, { waitUntil: 'domcontentloaded' })
  await page.locator('input[type="email"]').waitFor({ timeout: 30000 })
  await page.screenshot({ path: new URL('vite-signin.png', screenshots).pathname, fullPage: true })
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').first().fill(password)
  await page.locator('button[type="submit"]').first().click()
  await page.waitForURL(/\/apps(?:\?|$)/, { timeout: 30000 })
  await page.getByRole('region').first().waitFor({ timeout: 30000 })
  await page.waitForTimeout(2000)
  await page.screenshot({ path: new URL('vite-apps.png', screenshots).pathname, fullPage: true })
  const workflowHref = await page.locator('a[href*="/workflow"]').first().getAttribute('href')
  if (!workflowHref) throw new Error('No workflow link found on the app list')
  errors.length = 0
  failedResponses.length = 0
  await page.goto(new URL(workflowHref, baseUrl).href, { waitUntil: 'domcontentloaded' })
  await page.locator('.react-flow').waitFor({ timeout: 30000 })
  await page.waitForTimeout(3000)
  await page.screenshot({ path: new URL('vite-editor.png', screenshots).pathname, fullPage: true })
  errors.length = 0
  failedResponses.length = 0
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.locator('.react-flow').waitFor({ timeout: 30000 })
  await page.waitForTimeout(2000)

  const result = {
    url: page.url(),
    title: await page.title(),
    workflowHref,
    editorText: (await page.locator('body').innerText()).slice(0, 3000),
    errors,
    failedResponses: [...new Set(failedResponses)],
  }
  console.log(JSON.stringify(result, null, 2))
  if (errors.length || failedResponses.length) process.exitCode = 1
} catch (error) {
  console.error(JSON.stringify({
    failure: error.message,
    url: page.url(),
    body: (await page.locator('body').innerText()).slice(0, 3000),
    errors,
    failedResponses: [...new Set(failedResponses)],
  }, null, 2))
  await page.screenshot({ path: new URL('vite-failure.png', screenshots).pathname, fullPage: true })
  throw error
} finally {
  await browser.close()
}
