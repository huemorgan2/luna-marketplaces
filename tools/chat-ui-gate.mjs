#!/usr/bin/env node
/**
 * Release gate for the core ↔ plugin-chat-ui React coupling (plan 008).
 *
 * plugin-chat-ui mounts NATIVELY in the shell's React tree: its bundle is an
 * IIFE with react/react-dom as externals resolved to the shell's
 * window.LunaReact* globals. A core release that bumps the React major or
 * renames the exposed globals silently breaks the mount (soft-fails to
 * BasicChat). This gate makes that breakage loud at PR time:
 *
 *   1. React major must match between luna/ui and plugin ui-src.
 *   2. Core main.tsx must still assign the three globals.
 *   3. The plugin's vite externals must map to exactly those global names.
 *   4. The plugin bundle must build, register LunaChatUI.ChatPanel, and
 *      reference the globals.
 *
 * Run from the repo root: node tools/chat-ui-gate.mjs [--no-build]
 */
import { execSync } from 'node:child_process'
import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CORE_UI = path.join(ROOT, 'luna', 'ui')
const PLUGIN = path.join(ROOT, 'marketplace-src', 'plugin_chat_ui')
const UI_SRC = path.join(PLUGIN, 'ui-src')

const failures = []
const check = (ok, msg) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${msg}`)
  if (!ok) failures.push(msg)
}

const major = (range) => {
  const m = /(\d+)/.exec(String(range || ''))
  return m ? Number(m[1]) : NaN
}

// 1. React major parity.
const coreDeps = JSON.parse(readFileSync(path.join(CORE_UI, 'package.json'), 'utf8')).dependencies
const pluginDev = JSON.parse(readFileSync(path.join(UI_SRC, 'package.json'), 'utf8')).devDependencies
check(
  major(coreDeps.react) === major(pluginDev.react),
  `React major parity: core ${coreDeps.react} vs plugin ${pluginDev.react}`,
)

// 2. Core still exposes the globals the plugin binds to.
const mainTsx = readFileSync(path.join(CORE_UI, 'src', 'main.tsx'), 'utf8')
const GLOBALS = ['LunaReact', 'LunaReactDOM', 'LunaReactJsxRuntime']
for (const g of GLOBALS) {
  check(new RegExp(`window as any\\)\\.${g}\\s*=`).test(mainTsx), `core main.tsx assigns window.${g}`)
}

// 3. Plugin externals map to exactly those names.
const viteCfg = readFileSync(path.join(UI_SRC, 'vite.config.ts'), 'utf8')
for (const [mod, g] of [
  ['react', 'LunaReact'],
  ["'react-dom'", 'LunaReactDOM'],
  ["'react/jsx-runtime'", 'LunaReactJsxRuntime'],
]) {
  check(viteCfg.includes(g), `plugin vite config maps ${mod} → ${g}`)
}

// 4. Build the bundle against the current source and inspect it.
if (!process.argv.includes('--no-build')) {
  const hasModules = existsSync(path.join(UI_SRC, 'node_modules'))
  execSync(`npm ${hasModules ? 'install --no-audit --no-fund' : 'ci'} && npm run build`, {
    cwd: UI_SRC,
    stdio: 'inherit',
  })
}
const bundle = readFileSync(path.join(PLUGIN, 'ui', 'chat.js'), 'utf8')
check(/var LunaChatUI\s*=/.test(bundle), 'bundle registers window.LunaChatUI')
check(bundle.includes('ChatPanel'), 'bundle exports ChatPanel')
check(
  bundle.includes('LunaReactJsxRuntime') && bundle.includes('LunaReact'),
  'bundle binds to the shell React globals (not its own copy)',
)
check(!/react-dom\/cjs|scheduler\.production/.test(bundle), 'bundle did not inline React itself')

if (failures.length) {
  console.error(`\nchat-ui gate: ${failures.length} check(s) failed — a core change broke the`)
  console.error('exposed React contract. Fix the exposure or rebuild/republish plugin-chat-ui.')
  process.exit(1)
}
console.log('\nchat-ui gate: all checks passed')
