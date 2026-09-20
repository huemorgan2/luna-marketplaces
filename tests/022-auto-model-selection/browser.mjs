import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const core = process.env.LUNA_CORE_DIR || fileURLToPath(new URL('../../luna/', import.meta.url))
const require = createRequire(path.join(core, 'dojo/package.json'))
const { chromium } = require('playwright')
const shots = new URL('./shots/', import.meta.url)
await fs.mkdir(shots, { recursive: true })
const rows = [
  ['openai', 'gpt-5.5', 'GPT-5.5'], ['openai', 'gpt-4o', 'GPT-4o'],
  ['anthropic', 'claude-opus-4-6', 'Claude Opus 4.6'],
  ['anthropic', 'claude-sonnet-4-5-20250929', 'Claude Sonnet 4.5'],
  ['moonshot', 'kimi-k3', 'Kimi K3'], ['moonshot', 'kimi-k2.7-code', 'Kimi K2.7 Code'],
  ['qwen', 'qwen3.8-max', 'Qwen3.8 Max'], ['qwen', 'qwen3.7-flash', 'Qwen3.7 Flash'],
  ['xai', 'grok-4.5', 'Grok 4.5'], ['xai', 'grok-4.3', 'Grok 4.3'],
].map(([provider, model, label]) => ({ provider, model, label, fqn: `${provider}:${model}`, context_window: 200000 }))
let selection = 'auto'
let chain = ['anthropic:claude-opus-4-6', 'openai:gpt-4o']
const writes = []
const browser = await chromium.launch({ headless: false })
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } })
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/models**', async route => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (request.method() === 'PUT') {
      const body = request.postDataJSON()
      writes.push(body)
      selection = body.selection
      if (body.chain.length) chain = body.chain
      await route.fulfill({ json: { updated: true, selection } })
    } else if (pathname.endsWith('/catalog')) {
      await route.fulfill({ json: { catalog: { reasoning: rows, summarization: [], embedding: [] }, configured_providers: ['openai', 'anthropic', 'moonshot', 'qwen', 'xai'], window_caps: {} } })
    } else {
      await route.fulfill({ json: [{ purpose: 'reasoning', selection, chain: chain.map(fqn => rows.find(r => r.fqn === fqn)), fallback_policy: 'availability' }] })
    }
  })
  const url = process.env.AUTO_PICKER_URL || 'http://127.0.0.1:5187/auto-picker-fixture.html'
  await page.goto(url)
  const trigger = page.getByTestId('composer-model-select')
  await trigger.filter({ hasText: 'AUTO' }).waitFor()
  await trigger.click()
  assert.equal(await page.getByRole('option').first().getAttribute('aria-selected'), 'true')
  await page.screenshot({ path: new URL('auto-desktop.png', shots).pathname, animations: 'disabled' })
  await page.getByText('GPT-4o', { exact: true }).click()
  await trigger.filter({ hasText: 'GPT-4o' }).waitFor()
  await page.waitForFunction(() => !document.querySelector('[data-testid="composer-model-select"]').disabled)
  assert.equal(writes.at(-1).selection, 'manual')
  assert.equal(chain[0], 'openai:gpt-4o')
  await page.reload()
  await trigger.filter({ hasText: 'GPT-4o' }).waitFor()
  await trigger.click()
  await page.getByTestId('model-auto-option').click()
  await trigger.filter({ hasText: 'AUTO' }).waitFor()
  await page.waitForFunction(() => !document.querySelector('[data-testid="composer-model-select"]').disabled)
  assert.equal(writes.at(-1).selection, 'auto')
  assert.deepEqual(writes.at(-1).chain, [])
  await page.reload()
  await trigger.filter({ hasText: 'AUTO' }).waitFor()
  await page.setViewportSize({ width: 390, height: 844 })
  await trigger.click()
  const menu = page.getByRole('listbox')
  const box = await menu.boundingBox()
  assert.ok(box.x >= 0 && box.x + box.width <= 391)
  await page.screenshot({ path: new URL('auto-mobile.png', shots).pathname, animations: 'disabled' })
  assert.deepEqual(errors, [])
  await fs.writeFile(new URL('./browser-results.json', import.meta.url), JSON.stringify({ passed: true, writes, checks: ['auto default', 'manual persists', 'auto mode-only write', 'auto persists', 'mobile menu bounds', 'no page errors'], provider_calls: 'mocked' }, null, 2) + '\n')
  console.log('PASS: AUTO/manual persistence, mode-only writes, responsive bounds, no browser errors.')
} finally {
  await browser.close()
}
