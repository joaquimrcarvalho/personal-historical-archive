#!/usr/bin/env node
// Offline self-check for the dsh-pha plugin: no harness, no archive, no network.
//
//   node dsh-pha/scripts/smoke.mjs
//
// Why this exists: a profile install links this package (`pnpm add link:…`), so the
// harness ESM loader resolves it to this repository. Any bare import of a harness
// package (`@deepseek-ai/dsh-tools`) is then looked up from HERE — outside the
// profile's node_modules — and fails, and a single unloadable row makes the whole
// harness refuse to start. The first check below is the one that caught that.
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const failures = []
const check = (cond, label) => {
  if (cond) console.log('  ok   ' + label)
  else { failures.push(label); console.log('  FAIL ' + label) }
}

console.log('dsh-pha self-check — ' + root)

// 1. Zero dependencies: the package must import nothing.
const src = readFileSync(join(root, 'lib', 'index.js'), 'utf8')
check(!/(^|\n)\s*import\s/.test(src), 'lib/index.js imports nothing (a linked install cannot resolve harness peers)')
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
check(Object.keys(pkg.dependencies || {}).length === 0, 'package.json declares no runtime dependencies')
check(Object.keys(pkg.peerDependencies || {}).length === 0, 'package.json declares no peer dependencies')

const mod = await import(join(root, 'lib', 'index.js'))
check(typeof mod.apply === 'function', 'exports apply()')
check(mod.name === 'dsh-pha', 'exports name')
check(Array.isArray(mod.inject) && mod.inject.includes('subprocess') && mod.inject.includes('tools'),
  'declares inject [subprocess, tools]')

// 2. Compose it against stub services and inspect what it publishes.
const tools = []
const routes = []
const ctx = {
  subprocess: {
    resolveExecutable: async () => null,
    spawn: () => { throw new Error('the self-check never spawns') },
  },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => {} },
  tools: { register: (def) => { tools.push(def); return () => {} } },
  inject: (deps, fn) => {
    if (deps.includes('webServer')) {
      fn({
        webServer: { register: (route) => { routes.push(route.path); return () => {} } },
        get: () => undefined,
        effect: (f) => f(),
      })
    }
  },
}
const plugin = mod.apply(ctx)
check(plugin && typeof plugin.dispose === 'function', 'apply() returns a disposer')

// 3. Tool definitions are the raw JSON Schema the registry validates, not a spec.
const EXPECTED_TOOLS = ['pha_status', 'pha_documents', 'pha_document', 'pha_page', 'pha_search',
  'pha_open', 'pha_pending', 'pha_archive', 'pha_job_start', 'pha_job_status', 'pha_job_kill']
check(tools.length === EXPECTED_TOOLS.length, `registers ${EXPECTED_TOOLS.length} tools (got ${tools.length})`)
for (const name of EXPECTED_TOOLS) check(tools.some((t) => t.name === name), 'tool ' + name)
for (const t of tools) {
  const p = t.parameters || {}
  const schemaOk = p.type === 'object' && !!p.properties && typeof p.properties === 'object'
    && (p.required === undefined || Array.isArray(p.required))
  const defOk = typeof t.description === 'string' && typeof t.execute === 'function'
    && t.output && typeof t.output.render === 'function' && !!t.output.schema
  check(schemaOk && defOk, 'definition shape: ' + t.name)
  const badProps = Object.entries(p.properties || {})
    .filter(([, spec]) => typeof spec.type !== 'string' || Array.isArray(spec.type))
    .map(([k]) => k)
  check(badProps.length === 0, `property types are scalar JSON-Schema types: ${t.name}${badProps.length ? ' (' + badProps.join(', ') + ')' : ''}`)
  const orphans = (p.required || []).filter((k) => !(k in (p.properties || {})))
  check(orphans.length === 0, `required names are declared properties: ${t.name}${orphans.length ? ' (' + orphans.join(', ') + ')' : ''}`)
}

// 4. The same-origin routes the view fetches.
const EXPECTED_ROUTES = ['/pha/status', '/pha/archive', '/pha/documents', '/pha/document', '/pha/page',
  '/pha/search', '/pha/pageImage', '/pha/open', '/pha/pending', '/pha/config', '/pha/notes', '/pha/note',
  '/pha/defs', '/pha/def', '/pha/collectionEncoders']
for (const path of EXPECTED_ROUTES) check(routes.includes(path), 'route ' + path)

// 5. The client half stays loadable as a harness client module.
const bundle = readFileSync(join(root, 'lib', 'client.js'), 'utf8')
check(bundle.includes('__ModuleLoader__'), 'client bundle uses the harness module loader')
check(/exports\.apply\s*=/.test(bundle), 'client bundle exports a named apply')
check(/exports\.inject\s*=/.test(bundle), 'client bundle exports a named inject')
check(/inject\s*=\s*\[\s*["']slots["']/.test(bundle), 'client bundle injects the slots service')
check(bundle.includes('conversation.view'), 'client bundle registers conversation.view')
check(/require\(["']react["']\)/.test(bundle), 'client bundle keeps react external')

console.log('')
if (failures.length) {
  console.log(`${failures.length} check(s) FAILED`)
  process.exit(1)
}
console.log(`all checks passed — ${tools.length} tools, ${routes.length} routes, client bundle present`)
