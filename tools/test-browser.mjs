import { createRequire } from 'node:module'
import { existsSync } from 'node:fs'
import { appendFile, mkdir, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const localNodeBin = path.join(root, '.tools/fnm/node-versions/v24.22.3/installation/bin')
const browserPath = path.join(root, '.tools/playwright')
const outputDir = path.join(root, '.data/test')
const logPath = path.join(outputDir, 'browser.log')
const env = {
  ...process.env,
  PATH: `${localNodeBin}${path.delimiter}${process.env.PATH || ''}`,
  PLAYWRIGHT_BROWSERS_PATH: browserPath,
}
await mkdir(outputDir, { recursive: true })
await writeFile(logPath, '')
const require = createRequire(path.join(root, 'web-legacy/package.json'))
const { chromium } = require('playwright')

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: root,
    env,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    timeout: 180000,
    ...options,
  })
}

if (!existsSync(chromium.executablePath())) {
  console.log('[browser] Installing the Playwright Chromium runtime')
  const installed = run('pnpm', ['-C', 'web-legacy', 'exec', 'playwright', 'install', 'chromium'], {
    stdio: 'inherit',
  })
  if (installed.status !== 0) process.exit(installed.status || 1)
}

async function collect(directory) {
  const specs = []
  async function walk(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name)
      if (entry.isDirectory()) await walk(absolute)
      else if (/\.browser\.spec\.tsx?$/.test(entry.name)) specs.push(absolute)
    }
  }
  await walk(directory)
  return specs
}

const specs = [
  ...await collect(path.join(root, 'web-legacy/app')),
  ...await collect(path.join(root, 'web-legacy/features')),
].sort()
if (!specs.length) throw new Error('No browser component specifications found')
const startedAt = Date.now()
for (const [index, spec] of specs.entries()) {
  const relative = path.relative(path.join(root, 'web-legacy'), spec)
  console.log(`[browser ${index + 1}/${specs.length}] ${relative}`)
  const result = run('pnpm', ['-C', 'web-legacy', 'exec', 'vp', 'test', 'run', '--project', 'browser', relative, '--maxWorkers=1'])
  const output = `${result.stdout || ''}${result.stderr || ''}`
  await appendFile(logPath, `\n===== ${relative} =====\n${output}`)
  if (result.status !== 0) {
    console.error(output)
    if (result.error) console.error(result.error)
    process.exit(result.status || 1)
  }
}
console.log(`[browser] ${specs.length} isolated specifications passed in ${Math.round((Date.now() - startedAt) / 1000)}s`)
console.log(`[browser] Logs: ${path.relative(root, logPath)}`)
