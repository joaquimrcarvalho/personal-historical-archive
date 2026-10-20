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
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { Writable } from 'node:stream'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const failures = []
const check = (cond, label) => {
  if (cond) console.log('  ok   ' + label)
  else { failures.push(label); console.log('  FAIL ' + label) }
}

console.log('dsh-pha self-check — ' + root)

// 1. Zero dependencies: the package may import only node: builtins.
const src = readFileSync(join(root, 'lib', 'index.js'), 'utf8')
const froms = [...src.matchAll(/(?:^|\n)\s*import\s+[^;\n]*?\bfrom\s+[\x27\x22]([^\x27\x22]+)[\x27\x22]/g)].map((m) => m[1])
check(src.includes('homedir()'), 'child cwd falls back to the user home, not /')
check(froms.every((spec) => spec.startsWith("node:")), "lib/index.js imports only node: builtins (no harness peers)")
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
  'pha_open', 'pha_pending', 'pha_bib', 'pha_archive', 'pha_job_start', 'pha_job_status', 'pha_job_kill']
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
  '/pha/search', '/pha/pageImage', '/pha/render', '/pha/open', '/pha/pending', '/pha/config', '/pha/notes',
  '/pha/note', '/pha/defs', '/pha/def', '/pha/collectionEncoders', '/pha/bib', '/pha/inbox/plan',
  '/pha/inbox/move']
for (const path of EXPECTED_ROUTES) check(routes.includes(path), 'route ' + path)

// 4b. A configured projectRoot becomes the child cwd and PHA_HOME, so a
// built/installed pha can find config.yaml even when the harness cwd is `/`.
const spawns = []
const spawnTools = []
const spawnCtx = {
  subprocess: {
    resolveExecutable: async (cand) => (cand === 'pha' ? '/usr/bin/pha' : null),
    spawn: (spec) => {
      spawns.push(spec)
      const reader = (text) => ({ readFrom: () => ({ text, nextOffset: text.length, lossy: false }) })
      const stdout = JSON.stringify({ archive: '/tmp/pha-checkout-archive', ok: true, broken: [], engines: [] })
      return {
        collected: { stdout: reader(stdout), stderr: reader('') },
        done: Promise.resolve({ exitCode: 0, signal: null }),
      }
    },
  },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => {} },
  tools: { register: (def) => { spawnTools.push(def); return () => {} } },
  inject: () => {},
}
const spawnPlugin = mod.apply(spawnCtx, { projectRoot: '/tmp/pha-checkout', archiveDir: '/tmp/pha-archive' })
const archiveTool = spawnTools.find((t) => t.name === 'pha_archive')
await archiveTool.execute({})
check(spawns.length === 1, 'projectRoot config spawns pha')
check(spawns[0].cwd === '/tmp/pha-checkout', 'projectRoot config sets child cwd')
check(spawns[0].env && spawns[0].env.PHA_HOME === '/tmp/pha-checkout', 'projectRoot config pins child PHA_HOME')
check(spawns[0].env && spawns[0].env.PHA_ARCHIVE_DIR === '/tmp/pha-archive', 'archiveDir config pins child PHA_ARCHIVE_DIR')
spawnPlugin.dispose()

// 4c. A page image is served as BINARY on its own route.
//
// Why this check exists: the view used to receive the image as a base64 data URL in a
// JSON body, produced by a Python child whose stdout is capped at 4 MB. Past the cap
// `readFrom(0)` returns only the retained TAIL (the head, carrying the `data:` prefix,
// is dropped) and flags `lossy` — which the plugin ignored, so a large rendered page
// reached the browser as a bare base64 tail and the <img> showed nothing. The render
// route streams the file instead, so size cannot corrupt it.
const IMG_SHA = 'a'.repeat(64)
const imgArchive = mkdtempSync(join(tmpdir(), 'pha-render-'))
mkdirSync(join(imgArchive, 'renders', IMG_SHA), { recursive: true })
// Bigger than the 4 MB cap once base64-encoded (3 MB of bytes -> 4 MB of base64).
const IMG_BYTES = Buffer.alloc(3 * 1024 * 1024 + 7, 7)
writeFileSync(join(imgArchive, 'renders', IMG_SHA, 'p001.jpg'), IMG_BYTES)

const renderRoutes = []
const renderCtx = {
  subprocess: {
    resolveExecutable: async (cand) => (
      cand === 'pha' ? '/usr/bin/pha' : (/python/.test(cand) ? '/usr/bin/python3' : null)),
    spawn: (spec) => {
      const isInfo = spec.argv.includes('info')
      // The DB script's last argv is the page number: page 2 has no metadata, which is
      // how "this page has no render" is exercised.
      const noMeta = !isInfo && spec.argv[spec.argv.length - 1] === '2'
      const out = isInfo
        ? JSON.stringify({ archive_dir: imgArchive, db_path: join(imgArchive, 'archive.db'), configured: true })
        : (noMeta ? 'null' : JSON.stringify({ sha256: IMG_SHA, source_name: null }))
      const reader = (text) => ({ readFrom: () => ({ text, nextOffset: text.length, lossy: false }) })
      return {
        collected: { stdout: reader(out), stderr: reader('') },
        done: Promise.resolve({ exitCode: 0, signal: null }),
      }
    },
  },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => {} },
  tools: { register: () => () => {} },
  inject: (deps, fn) => {
    if (deps.includes('webServer')) {
      fn({
        webServer: { register: (r) => { renderRoutes.push(r); return () => {} } },
        get: () => undefined,
        effect: (f) => f(),
      })
    }
  },
}
const renderPlugin = mod.apply(renderCtx, { projectRoot: '/tmp/pha-checkout' })
const renderRoute = renderRoutes.find((r) => r.path === '/pha/render')
check(!!renderRoute && typeof renderRoute.handler === 'function',
  'route /pha/render is registered with a handler')

const fetchRender = async (url) => {
  const chunks = []
  const res = new Writable({ write(chunk, enc, cb) { chunks.push(chunk); cb() } })
  let status = null
  let headers = null
  res.writeHead = (code, hdrs) => { status = code; headers = hdrs || {} }
  const finished = new Promise((resolve) => res.on('finish', resolve))
  renderRoute.handler({ url }, res)
  await finished
  return { status, headers, body: Buffer.concat(chunks) }
}

if (renderRoute) {
  const ok = await fetchRender('/pha/render?doc=7&page=1')
  check(ok.status === 200, 'a page with a render answers 200')
  check(ok.body.length === IMG_BYTES.length && ok.body.equals(IMG_BYTES),
    `the whole image arrives (${ok.body.length} of ${IMG_BYTES.length} bytes) — no stdout cap`)
  check(ok.headers && ok.headers['content-type'] === 'image/jpeg', 'the render is served as image/jpeg')
  check(ok.headers && ok.headers['content-length'] === String(IMG_BYTES.length),
    'content-length matches the file on disk')

  const missing = await fetchRender('/pha/render?doc=7&page=2')
  check(missing.status === 404, 'a page with no render answers 404, not a corrupt image')
}
renderPlugin.dispose()
rmSync(imgArchive, { recursive: true, force: true })

// 5. The client half stays loadable as a harness client module.
const bundle = readFileSync(join(root, 'lib', 'client.js'), 'utf8')
check(bundle.includes('__ModuleLoader__'), 'client bundle uses the harness module loader')
check(/exports\.apply\s*=/.test(bundle), 'client bundle exports a named apply')
check(/exports\.inject\s*=/.test(bundle), 'client bundle exports a named inject')
check(/inject\s*=\s*\[\s*["']slots["']/.test(bundle), 'client bundle injects the slots service')
check(bundle.includes('conversation.view'), 'client bundle registers conversation.view')
check(/require\(["']react["']\)/.test(bundle), 'client bundle keeps react external')
check(bundle.includes('function slugifyPath'), 'client bundle inlines the link helpers')
check(bundle.includes('function restorePlan'), 'client bundle inlines the reading-position memory')
check(bundle.includes('function askContext'), 'client bundle inlines the ask-context helper')
check(bundle.includes('function parseNumberQuery'), 'client bundle inlines the document-number query rule')
check(bundle.includes('function statusUnscanned'), 'client bundle inlines the status-derived sections')
check(!/from\s*["']\.\/(links|viewmemory|askcontext|query|statusview)\.js["']/.test(bundle), 'client bundle has no leftover inline-module import')
check(!/^\s*import\s/m.test(bundle), 'client bundle imports nothing')

console.log('')
if (failures.length) {
  console.log(`${failures.length} check(s) FAILED`)
  process.exit(1)
}
console.log(`all checks passed — ${tools.length} tools, ${routes.length} routes, client bundle present`)
