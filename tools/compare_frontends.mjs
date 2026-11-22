import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../web-legacy/package.json', import.meta.url))
const { chromium } = require('playwright')

const outputDir = new URL('../.data/acceptance/frontend-matrix/', import.meta.url)
await mkdir(outputDir, { recursive: true })

const targets = [
  { name: 'vite', baseUrl: process.env.WORKFLOW_VITE_URL || 'http://127.0.0.1' },
  { name: 'legacy', baseUrl: process.env.WORKFLOW_LEGACY_URL || 'http://legacy.localhost' },
]
const email = process.env.WORKFLOW_ADMIN_EMAIL || 'admin@example.com'
const password = process.env.WORKFLOW_ADMIN_PASSWORD || 'Password123'
const executablePath = process.env.CHROME_PATH || undefined
const browser = await chromium.launch({ headless: true, executablePath })
const reports = {}

const normalizeText = (value, fixture) => {
  let normalized = value
  if (fixture) {
    normalized = normalized.replaceAll(fixture.appId, '<app-id>')
    const token = fixture.publicPath.split('/').pop()
    normalized = normalized.replaceAll(token, '<public-token>')
  }
  return normalized
    .replace(/\b\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?\b/gi, '<date-time>')
    .replace(/\b\d{1,2}\/\d{1,2}\/\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?/gi, '<date-time>')
    .replace(/\b\d{1,2}:\d{2}:\d{2}(?:\s*[AP]M)?\b/gi, '<time>')
    .replace(/\b(?:\d+(?:\.\d+)?|a|an)(?:\s+few)?\s+(?:second|minute|hour|day|month|year)s?\s+ago\b/gi, '<relative-time>')
    .replace(/\b(?:just now)\b/gi, '<relative-time>')
    .replace(/\r\n/g, '\n')
    .trim()
}

const fileHash = async (path) => createHash('sha256').update(await readFile(path)).digest('hex')

async function provisionFixture() {
  const context = await browser.newContext({ locale: 'en-US' })
  const api = context.request
  const baseUrl = targets[0].baseUrl
  let appId
  let headers = {}
  try {
    const login = await api.post(`${baseUrl}/console/api/login`, {
      data: { email, password: Buffer.from(password).toString('base64') },
    })
    if (!login.ok()) throw new Error(`Fixture login failed: ${login.status()} ${await login.text()}`)
    const csrf = (await context.cookies(baseUrl)).find((cookie) => ['csrf_token', '__Host-csrf_token'].includes(cookie.name))?.value
    if (!csrf) throw new Error('Fixture login did not issue a CSRF cookie')
    headers = { 'X-CSRF-Token': csrf }
    const created = await api.post(`${baseUrl}/console/api/apps`, {
      headers,
      data: {
        name: 'Frontend Parity Acceptance',
        mode: 'workflow',
        description: 'Isolated browser comparison fixture; removed after the run',
      },
    })
    if (!created.ok()) throw new Error(`Fixture creation failed: ${created.status()} ${await created.text()}`)
    const app = await created.json()
    appId = app.id
    const fixture = JSON.parse(await readFile(new URL('../contracts/fixtures/reference-workflow.json', import.meta.url), 'utf8'))
    const saved = await api.post(`${baseUrl}/console/api/apps/${appId}/workflows/draft`, {
      headers,
      data: { graph: fixture.graph, features: {}, conversation_variables: [], hash: null },
    })
    if (!saved.ok()) throw new Error(`Fixture draft failed: ${saved.status()} ${await saved.text()}`)
    const published = await api.post(`${baseUrl}/console/api/apps/${appId}/workflows/publish`, { headers, data: {} })
    if (!published.ok()) throw new Error(`Fixture publish failed: ${published.status()} ${await published.text()}`)
    let detail
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const detailResponse = await api.get(`${baseUrl}/console/api/apps/${appId}`, { headers })
      if (!detailResponse.ok()) throw new Error(`Fixture detail failed: ${detailResponse.status()} ${await detailResponse.text()}`)
      detail = await detailResponse.json()
      if (detail.site?.access_token) break
      await new Promise((resolve) => setTimeout(resolve, 100))
    }
    if (!detail?.site?.access_token) throw new Error('Fixture did not issue a public workflow token')
    return {
      context,
      csrf,
      baseUrl,
      appId,
      appHref: `/app/${appId}/workflow`,
      publicPath: `/workflow/${detail.site.access_token}`,
    }
  } catch (error) {
    if (appId) await api.delete(`${baseUrl}/console/api/apps/${appId}`, { headers })
    await context.close()
    throw error
  }
}

async function runTarget(target, fixture) {
  const context = await browser.newContext({
    locale: 'en-US',
    viewport: { width: 1440, height: 1000 },
    colorScheme: 'light',
    reducedMotion: 'reduce',
    acceptDownloads: true,
  })
  const report = { ...target, scenarios: {}, appHref: fixture.appHref, publicPath: fixture.publicPath }
  const appHref = fixture.appHref
  const appId = fixture.appId

  async function scenario(name, path, action, options = {}) {
    const page = await context.newPage()
    const errors = []
    const failedResponses = []
    const requests = []
    page.on('pageerror', (error) => errors.push(error.stack || error.message))
    page.on('console', (message) => {
      const expectedNotFound = options.expectedDocumentStatus === 404 && message.text().includes('Failed to load resource')
      if (message.type() === 'error' && !expectedNotFound) errors.push(`${message.text()} ${JSON.stringify(message.location())}`)
    })
    page.on('response', (response) => {
      const url = new URL(response.url())
      if (url.origin !== target.baseUrl) return
      const request = response.request()
      const item = `${request.method()} ${url.pathname}${url.search}`
      requests.push(`${response.status()} ${item}`)
      const expectedNotFound = options.expectedDocumentStatus === response.status() && request.resourceType() === 'document'
      if (response.status() >= 400 && !expectedNotFound) failedResponses.push(`${response.status()} ${item}`)
    })
    const scenarioReport = { path, requests, errors, failedResponses }
    report.scenarios[name] = scenarioReport
    try {
      await page.goto(`${target.baseUrl}${path}`, { waitUntil: 'domcontentloaded' })
      if (options.editor) {
        await page.locator('.react-flow').waitFor({ timeout: 30000 })
        await page.locator('.react-flow__node').first().waitFor({ timeout: 30000 })
      } else if (options.apps) {
        await page.locator('a[href*="/workflow"]').first().waitFor({ timeout: 30000 })
      } else if (options.ready) {
        await options.ready(page)
      }
      if (action) await action(page, scenarioReport)
      if (options.editor) {
        await page.waitForFunction(() => [...document.querySelectorAll('[role="status"][aria-label="Loading"]')].every((element) => {
          const style = getComputedStyle(element)
          return style.display === 'none' || style.visibility === 'hidden' || element.getBoundingClientRect().width === 0
        }))
      }
      await page.evaluate(() => document.fonts.ready)
      const screenshotPath = new URL(`${name}-${target.name}.png`, outputDir).pathname
      await page.screenshot({ path: screenshotPath, fullPage: true, animations: 'disabled', caret: 'hide' })
      const snapshot = await page.evaluate(() => {
        const isVisible = (element) => {
          const style = getComputedStyle(element)
          const rect = element.getBoundingClientRect()
          return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0
        }
        const controls = [...document.querySelectorAll('button, a, input, textarea, [role="menuitem"], [role="option"], [role="tab"]')]
          .filter(isVisible)
          .map((element) => ({
            tag: element.tagName.toLowerCase(),
            role: element.getAttribute('role'),
            name: element.getAttribute('aria-label') || element.textContent?.trim() || element.getAttribute('placeholder') || '',
            type: element.getAttribute('type'),
            accept: element.getAttribute('accept'),
            disabled: element.matches(':disabled'),
            expanded: element.getAttribute('aria-expanded'),
          }))
        const hit = document.elementFromPoint(window.innerWidth - 24, 24)
        return {
          path: location.pathname + location.search,
          body: document.body.innerText,
          controls,
          dialogs: [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')].filter(isVisible).map((element) => element.innerText),
          fileAccepts: [...document.querySelectorAll('input[type="file"]')].map((element) => element.getAttribute('accept')),
          topRightHit: hit ? `${hit.tagName.toLowerCase()}.${hit.className}` : null,
        }
      })
      scenarioReport.url = page.url()
      scenarioReport.body = normalizeText(snapshot.body, fixture)
      scenarioReport.controls = snapshot.controls.map((control) => ({ ...control, name: normalizeText(control.name, fixture) }))
      scenarioReport.dialogs = snapshot.dialogs.map((dialog) => normalizeText(dialog, fixture))
      scenarioReport.fileAccepts = snapshot.fileAccepts
      scenarioReport.topRightHit = snapshot.topRightHit
      scenarioReport.screenshot = screenshotPath
      scenarioReport.screenshotSha256 = await fileHash(screenshotPath)
      scenarioReport.ok = errors.length === 0 && failedResponses.length === 0
      if (options.cancel) {
        await page.keyboard.press('Escape')
        await page.waitForFunction(() => [...document.querySelectorAll('[role="dialog"], [role="alertdialog"], [role="menu"]')].every((element) => {
          const style = getComputedStyle(element)
          return style.display === 'none' || style.visibility === 'hidden' || element.getBoundingClientRect().width === 0
        }))
        scenarioReport.cancelled = true
      }
    } catch (error) {
      scenarioReport.ok = false
      scenarioReport.failure = error.stack || error.message
      scenarioReport.url = page.url()
      try {
        scenarioReport.body = normalizeText(await page.locator('body').innerText(), fixture)
        const failurePath = new URL(`${name}-${target.name}-failure.png`, outputDir).pathname
        await page.screenshot({ path: failurePath, fullPage: true, animations: 'disabled' })
        scenarioReport.screenshot = failurePath
      } catch {}
    } finally {
      await page.close()
    }
  }

  const loginPage = await context.newPage()
  const loginErrors = []
  loginPage.on('pageerror', (error) => loginErrors.push(error.message))
  await loginPage.goto(`${target.baseUrl}/signin`, { waitUntil: 'domcontentloaded' })
  await loginPage.locator('input[type="email"]').waitFor({ timeout: 30000 })
  const signinPath = new URL(`signin-${target.name}.png`, outputDir).pathname
  await loginPage.screenshot({ path: signinPath, fullPage: true, animations: 'disabled' })
  report.scenarios.signin = {
    path: '/signin',
    screenshot: signinPath,
    screenshotSha256: await fileHash(signinPath),
    body: normalizeText(await loginPage.locator('body').innerText()),
    errors: loginErrors,
    failedResponses: [],
    ok: loginErrors.length === 0,
  }
  await loginPage.locator('input[type="email"]').fill(email)
  await loginPage.locator('input[type="password"]').first().fill(password)
  await loginPage.locator('button[type="submit"]').first().click()
  await loginPage.waitForURL(/\/apps(?:\?|$)/, { timeout: 30000, waitUntil: 'domcontentloaded' })
  await loginPage.locator(`a[href="${appHref}"]`).waitFor({ timeout: 30000 })
  await loginPage.close()

  await scenario('apps', '/apps', null, { apps: true })
  await scenario('root-redirect', '/', (page) => page.waitForURL(/\/apps(?:\?|$)/, { waitUntil: 'domcontentloaded' }), { apps: true })
  await scenario('not-found', '/route-that-does-not-exist', async (page) => {
    await page.getByRole('heading', { name: '404', exact: true }).waitFor()
    await page.getByText('This page could not be found.', { exact: true }).waitFor()
  }, { expectedDocumentStatus: 404 })
  await scenario('auth-refresh', '/auth/refresh?redirect_url=/apps', (page) => page.waitForURL(/\/apps(?:\?|$)/, { timeout: 30000 }), { apps: true })

  await scenario('filter-types', '/apps', async (page) => {
    await page.getByRole('combobox', { name: 'Types', exact: true }).click()
    await page.getByRole('option', { name: 'Workflow', exact: true }).waitFor()
  }, { apps: true, cancel: true })
  await scenario('filter-creators', '/apps', async (page) => {
    await page.locator('button[aria-label="Creators"]').click()
    await page.locator('input[name="creator-query"]').waitFor()
  }, { apps: true, cancel: true })
  await scenario('filter-sort', '/apps', async (page) => {
    await page.getByRole('combobox', { name: /Sort by/i }).click()
    await page.getByRole('option', { name: 'Recently created', exact: true }).waitFor()
  }, { apps: true, cancel: true })
  await scenario('search', '/apps', async (page) => {
    const input = page.getByRole('searchbox', { name: 'Search Applications' })
    const response = page.waitForResponse((item) => item.url().includes('/console/api/apps') && item.url().includes('name=__workflow_acceptance_no_match__'))
    await input.fill('__workflow_acceptance_no_match__')
    await response
    await page.getByText('No apps here', { exact: true }).last().waitFor()
  }, { apps: false })
  await scenario('create-menu', '/apps', async (page) => {
    await page.getByRole('button', { name: 'Create', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Create from Blank', exact: true }).waitFor()
  }, { apps: true, cancel: true })
  await scenario('create-blank-dialog', '/apps', async (page) => {
    await page.getByRole('button', { name: 'Create', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Create from Blank', exact: true }).click()
    await page.getByRole('dialog').waitFor()
  }, { apps: true, cancel: true })
  await scenario('import-dialog', '/apps', async (page) => {
    await page.getByRole('button', { name: 'Create', exact: true }).click()
    await page.getByRole('menuitem', { name: /Import Workflow/ }).click()
    await page.getByRole('dialog').waitFor()
    const accepts = await page.locator('input[type="file"]').evaluateAll((inputs) => inputs.map((input) => input.getAttribute('accept')))
    if (accepts.length !== 1 || accepts[0] !== '.yaml,.yml') throw new Error(`Unexpected import formats: ${JSON.stringify(accepts)}`)
    if (await page.locator('input[type="url"]').count()) throw new Error('URL import is still present')
  }, { apps: true, cancel: true })
  await scenario('account-menu', '/apps', async (page) => {
    await page.getByRole('button', { name: /Account/ }).click()
    await page.getByRole('menuitem', { name: 'Log out', exact: true }).waitFor()
  }, { apps: true, cancel: true })

  async function openCardMenu(page) {
    const link = page.locator(`a[href="${appHref}"]`)
    const card = link.locator('xpath=ancestor::li[1]')
    await card.hover()
    const button = card.getByRole('button', { name: /More actions for/i })
    await button.click()
    await page.getByRole('menuitem', { name: 'Edit Info', exact: true }).waitFor()
    if (await page.getByRole('menuitem', { name: 'Resource Access', exact: true }).count())
      throw new Error('Resource Access must not be present on workflow cards')
  }
  await scenario('card-menu', '/apps', openCardMenu, { apps: true, cancel: true })
  await scenario('card-context-menu', '/apps', async (page) => {
    await page.locator(`a[href="${appHref}"]`).click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Edit Info', exact: true }).waitFor()
  }, { apps: true, cancel: true })
  for (const [name, menuName, role] of [
    ['card-edit-dialog', 'Edit Info', 'dialog'],
    ['card-duplicate-dialog', 'Duplicate', 'dialog'],
    ['card-delete-dialog', 'Delete', 'alertdialog'],
  ]) {
    await scenario(name, '/apps', async (page) => {
      await openCardMenu(page)
      await page.getByRole('menuitem', { name: menuName, exact: true }).click()
      await page.getByRole(role).waitFor()
    }, { apps: true, cancel: true })
  }
  await scenario('card-export', '/apps', async (page, current) => {
    await openCardMenu(page)
    const downloadPromise = page.waitForEvent('download', { timeout: 15000 }).catch(() => null)
    await page.getByRole('menuitem', { name: 'Export App', exact: true }).click()
    const download = await downloadPromise
    if (download) {
      const path = await download.path()
      current.download = { filename: download.suggestedFilename(), sha256: await fileHash(path) }
    } else {
      await page.getByRole('dialog').waitFor({ timeout: 5000 })
      current.download = { confirmationRequired: true }
    }
  }, { apps: true, cancel: true })

  await scenario('editor', appHref, null, { editor: true })
  await scenario('node-panel', appHref, async (page) => {
    await page.locator('.react-flow__node').first().click()
    await page.locator('.react-flow__node.selected').waitFor()
    await page.getByRole('button', { name: 'Add Input Field', exact: true }).waitFor()
  }, { editor: true })
  await scenario('node-actions', appHref, async (page) => {
    const node = page.locator('.react-flow__node').first()
    await node.click()
    await node.getByRole('button', { name: 'More', exact: true }).click()
    await page.getByRole('menu').waitFor()
  }, { editor: true, cancel: true })
  await scenario('node-catalog', appHref, async (page) => {
    await page.getByRole('button', { name: 'Add Node', exact: true }).first().click()
    const dialog = page.getByRole('dialog', { name: 'Add Node' })
    await dialog.waitFor()
    if (!await dialog.getByText('Doc Extractor', { exact: true }).count())
      throw new Error('Document Extractor must be present in the node catalog')
    if (!await dialog.getByText('Human Input', { exact: true }).count())
      throw new Error('Human Input must be present in the node catalog')
    if (await dialog.getByText('Knowledge Retrieval', { exact: true }).count())
      throw new Error('Knowledge Retrieval must not be present in the node catalog')
  }, { editor: true, cancel: true })
  for (const [name, label] of [
    ['node-catalog-tools', 'Tools'],
    ['node-catalog-start', 'Start'],
  ]) {
    await scenario(name, appHref, async (page) => {
      await page.getByRole('button', { name: 'Add Node', exact: true }).first().click()
      const dialog = page.getByRole('dialog', { name: 'Add Node' })
      await dialog.waitFor()
      await dialog.getByRole('tab', { name: label, exact: true }).click()
      await dialog.locator('[role="tab"][aria-selected="true"]').filter({ hasText: label }).waitFor()
    }, { editor: true, cancel: true })
  }
  await scenario('snippets-removed', appHref, async (page) => {
    await page.getByRole('button', { name: 'Add Node', exact: true }).first().click()
    const dialog = page.getByRole('dialog', { name: 'Add Node' })
    await dialog.waitFor()
    if (await dialog.getByRole('tab', { name: 'Snippets', exact: true }).count())
      throw new Error('Snippets entry must be removed with its unsupported APIs')
  }, { editor: true, cancel: true })
  await scenario('test-run', appHref, async (page) => {
    const menu = page.getByRole('menu')
    await page.getByRole('button', { name: /^Test Run/ }).click()
    await Promise.race([
      menu.waitFor(),
      page.getByText(/Test Run \(/).first().waitFor(),
    ])
    if (!await menu.count()) {
      const startRun = page.getByRole('button', { name: 'Start Run', exact: true })
      await startRun.waitFor()
      await page.getByRole('textbox', { name: 'Name', exact: true }).fill('World')
      await startRun.click()
      await page.getByText('Hello World', { exact: true }).waitFor()
    }
  }, { editor: true, cancel: true })
  await scenario('run-history', appHref, async (page) => {
    const response = page.waitForResponse((item) => item.url().includes('/workflow-runs'))
    await page.getByRole('button', { name: 'View run history', exact: true }).click()
    await response
    await page.getByText('Run History', { exact: true }).waitFor()
  }, { editor: true, cancel: true })
  await scenario('environment-panel', appHref, async (page) => {
    await page.getByRole('button', { name: 'Environment Variables', exact: true }).click()
    await page.getByText('Environment Variables', { exact: true }).waitFor()
  }, { editor: true })
  await scenario('system-variable-panel', appHref, async (page) => {
    const button = page.getByRole('button', { name: 'System Variables', exact: true })
    if (await button.count()) {
      await button.click()
      await page.getByText('System Variables', { exact: true }).waitFor()
    } else {
      currentOptionalSkip(report.scenarios['system-variable-panel'], 'System variables are not exposed for this workflow')
    }
  }, { editor: true })
  await scenario('variable-inspect-panel', appHref, async (page) => {
    const button = page.getByRole('button', { name: /^(Variable Inspect|View cached variables)$/ })
    if (await button.count()) {
      await button.first().click()
      await page.getByText('Variable Inspect', { exact: true }).first().waitFor()
    } else {
      currentOptionalSkip(report.scenarios['variable-inspect-panel'], 'Variable inspector is unavailable in the current read-only state')
    }
  }, { editor: true })
  await scenario('version-history', appHref, async (page) => {
    await page.getByRole('button', { name: 'Version History', exact: true }).click()
    await page.getByText('Versions', { exact: true }).first().waitFor()
  }, { editor: true })
  await scenario('checklist', appHref, async (page) => {
    await page.getByRole('button', { name: /^Checklist/ }).click()
    await page.getByText(/Checklist/, { exact: false }).first().waitFor()
  }, { editor: true, cancel: true })
  await scenario('publish-menu', appHref, async (page) => {
    await page.getByRole('button', { name: 'Publish', exact: true }).click()
    await page.getByText(/Latest Published|Current Draft|Publish Update/).first().waitFor()
  }, { editor: true, cancel: true })
  await scenario('workflow-tool-drawer', appHref, async (page) => {
    await page.getByRole('button', { name: 'Publish', exact: true }).click()
    await page.getByRole('button', { name: 'Workflow as Tool', exact: true }).click()
    await page.getByRole('dialog').waitFor()
  }, { editor: true, cancel: true })
  await scenario('access-point', `/app/${appId}/access-point`, async (page) => {
    await page.locator('#access-point-title').waitFor({ timeout: 30000 })
  })
  await scenario('access-point-qrcode', `/app/${appId}/access-point`, async (page) => {
    await page.getByRole('button', { name: 'Link QR Code', exact: true }).click()
    await page.getByRole('dialog').waitFor()
  }, { cancel: true })
  await scenario('access-point-regenerate', `/app/${appId}/access-point`, async (page) => {
    await page.getByRole('button', { name: 'Regenerate', exact: true }).click()
    await page.getByRole('alertdialog').waitFor()
  }, { cancel: true })
  if (report.publicPath) {
    await scenario('public-workflow', report.publicPath, async (page) => {
      await page.locator('form, input, textarea, button').first().waitFor({ timeout: 30000 })
    })
  }

  await context.close()
  return report
}

function currentOptionalSkip(report, reason) {
  report.optionalSkip = reason
}

try {
  for (const target of targets) {
    const fixture = await provisionFixture()
    try {
      reports[target.name] = await runTarget(target, fixture)
    } finally {
      const deleted = await fixture.context.request.delete(`${fixture.baseUrl}/console/api/apps/${fixture.appId}`, {
        headers: { 'X-CSRF-Token': fixture.csrf },
      })
      if (!deleted.ok()) throw new Error(`Fixture cleanup failed: ${deleted.status()} ${await deleted.text()}`)
      await fixture.context.close()
    }
  }
  const scenarioNames = [...new Set(Object.values(reports).flatMap((report) => Object.keys(report.scenarios)))]
  const comparisons = {}
  for (const name of scenarioNames) {
    const vite = reports.vite.scenarios[name]
    const legacy = reports.legacy.scenarios[name]
    if (!vite || !legacy) {
      comparisons[name] = { ok: false, reason: 'Scenario missing from one frontend' }
      continue
    }
    comparisons[name] = {
      ok: vite.ok !== false && legacy.ok !== false && vite.body === legacy.body && JSON.stringify(vite.controls) === JSON.stringify(legacy.controls),
      viteOk: vite.ok,
      legacyOk: legacy.ok,
      bodyEqual: vite.body === legacy.body,
      controlsEqual: JSON.stringify(vite.controls) === JSON.stringify(legacy.controls),
      screenshotEqual: vite.screenshotSha256 === legacy.screenshotSha256,
      viteFailure: vite.failure,
      legacyFailure: legacy.failure,
      viteErrors: vite.errors,
      legacyErrors: legacy.errors,
      viteFailedResponses: vite.failedResponses,
      legacyFailedResponses: legacy.failedResponses,
      viteOptionalSkip: vite.optionalSkip,
      legacyOptionalSkip: legacy.optionalSkip,
    }
  }
  const summary = {
    generatedAt: new Date().toISOString(),
    viewport: { width: 1440, height: 1000 },
    locale: 'en-US',
    scenarioCount: scenarioNames.length,
    comparisons,
    reports,
  }
  await writeFile(new URL('report.json', outputDir), JSON.stringify(summary, null, 2))
  console.log(JSON.stringify({
    scenarioCount: scenarioNames.length,
    comparisons: Object.fromEntries(Object.entries(comparisons).map(([name, value]) => [name, {
      ok: value.ok,
      bodyEqual: value.bodyEqual,
      controlsEqual: value.controlsEqual,
      screenshotEqual: value.screenshotEqual,
      viteFailure: value.viteFailure,
      legacyFailure: value.legacyFailure,
    }])),
    report: new URL('report.json', outputDir).pathname,
  }, null, 2))
  if (Object.values(comparisons).some((comparison) => !comparison.ok)) process.exitCode = 1
} finally {
  await browser.close()
}
