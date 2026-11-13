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
  'pha_open', 'pha_pending', 'pha_bib', 'pha_records', 'pha_archive', 'pha_job_start',
  'pha_job_status', 'pha_job_kill']
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
  '/pha/records', '/pha/recordcounts', '/pha/search', '/pha/pageImage', '/pha/render', '/pha/open',
  '/pha/source', '/pha/pending', '/pha/config', '/pha/notes', '/pha/note', '/pha/defs', '/pha/def',
  '/pha/collectionEncoders', '/pha/bib', '/pha/inbox/plan', '/pha/inbox/move']
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

// 4d. `/pha/source` opens a document's ORIGINAL (its PDF, image, or folder of page
// images) through `pha open --source`, and refuses a dropbox/inbox relative path that
// escapes its tree. The view puts a small "open" button on every document row, so this
// route is what makes it work for both archived documents and not-yet-scanned items.
const srcSpawns = []
const srcRoutes = []
const srcCtx = {
  subprocess: {
    resolveExecutable: async (cand) => (cand === 'pha' ? '/usr/bin/pha' : null),
    spawn: (spec) => {
      srcSpawns.push(spec)
      const isHelp = spec.argv[1] === 'open' && spec.argv[2] === '--help'
      const out = spec.argv.includes('info')
        ? JSON.stringify({ archive_dir: '/tmp/pha-src-archive', db_path: '/tmp/pha-src-archive/archive.db', configured: true })
        : isHelp
          ? 'usage: pha open [-h] [--edited] [--editor EDITOR] [--source] doc [page]'
          : 'opened: ' + spec.argv[spec.argv.length - 1]
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
        webServer: { register: (r) => { srcRoutes.push(r); return () => {} } },
        get: () => undefined,
        effect: (f) => f(),
      })
    }
  },
}
const srcPlugin = mod.apply(srcCtx, { projectRoot: '/tmp/pha-checkout' })
const sourceRoute = srcRoutes.find((r) => r.path === '/pha/source')
check(!!sourceRoute && typeof sourceRoute.handler === 'function',
  'route /pha/source is registered with a handler')

const fetchJsonRoute = async (route, url) => {
  let body = ''
  const res = new Writable({ write(chunk, enc, cb) { body += chunk; cb() } })
  let status = null
  res.writeHead = (code) => { status = code }
  const finished = new Promise((resolve) => res.on('finish', resolve))
  route.handler({ url }, res)
  await finished
  let data = null
  try { data = JSON.parse(body) } catch (e) { data = null }
  return { status, body: data }
}
const fetchSourceJson = (url) => fetchJsonRoute(sourceRoute, url)

if (sourceRoute) {
  const beforeDoc = srcSpawns.length
  const doc = await fetchSourceJson('/pha/source?doc=18')
  check(doc.status === 200 && doc.body && doc.body.ok === true, 'the doc form answers ok')
  const docArgv = srcSpawns.slice(beforeDoc).map((s) => s.argv).find((a) => a[1] === 'open' && a[2] === '--source')
  check(!!docArgv && docArgv[docArgv.length - 1] === '18',
    'the doc form runs `pha open --source <doc>`')

  const beforeRel = srcSpawns.length
  const rel = await fetchSourceJson('/pha/source?under=dropbox&rel=collections/COLX/d.pdf')
  check(rel.status === 200 && rel.body && rel.body.ok === true, 'the dropbox-relative form answers ok')
  const relArgv = srcSpawns.slice(beforeRel).map((s) => s.argv).find((a) => a[1] === 'open' && a[2] === '--source')
  check(!!relArgv && relArgv[relArgv.length - 1] === '/tmp/pha-src-archive/dropbox/collections/COLX/d.pdf',
    'the dropbox-relative form opens the item under dropbox/')

  const beforeEscape = srcSpawns.length
  const escape = await fetchSourceJson('/pha/source?under=dropbox&rel=../../etc/passwd')
  check(escape.status === 500 && escape.body && escape.body.ok === false
    && /escapes/.test(String(escape.body.error || '')),
    'a rel that escapes dropbox/ is refused')
  check(srcSpawns.length === beforeEscape, 'the refused rel spawns nothing')

  const beforeUnder = srcSpawns.length
  const badUnder = await fetchSourceJson('/pha/source?under=library&rel=x')
  check(badUnder.body && badUnder.body.ok === false, 'an unknown `under` is refused')
  check(srcSpawns.length === beforeUnder, 'the unknown `under` spawns nothing')
}
srcPlugin.dispose()

// 4e. A view NEWER than the installed CLI: `pha open --source` does not exist in
// 0.46.x, so argparse answers "unrecognized arguments: --source" and prints the
// whole usage. That was the reported "nothing happens" in the PHA view — the route
// must name the cure (`pha update`), not hand back the usage wall.
const oldSpawns = []
const oldRoutes = []
const oldCtx = {
  subprocess: {
    resolveExecutable: async (cand) => (cand === 'pha' ? '/usr/bin/pha' : null),
    spawn: (spec) => {
      oldSpawns.push(spec)
      const isHelp = spec.argv[1] === 'open' && spec.argv[2] === '--help'
      const out = isHelp
        ? 'usage: pha open [-h] [--edited] [--editor EDITOR] doc [page]'
        : 'usage: pha [-h] [--version] {scan,...} ...\npha: error: unrecognized arguments: --source'
      const reader = (text) => ({ readFrom: () => ({ text, nextOffset: text.length, lossy: false }) })
      return {
        collected: { stdout: reader(out), stderr: reader(isHelp ? '' : out) },
        done: Promise.resolve({ exitCode: isHelp ? 0 : 2, signal: null }),
      }
    },
  },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => {} },
  tools: { register: () => () => {} },
  inject: (deps, fn) => {
    if (deps.includes('webServer')) {
      fn({
        webServer: { register: (r) => { oldRoutes.push(r); return () => {} } },
        get: () => undefined,
        effect: (f) => f(),
      })
    }
  },
}
const oldPlugin = mod.apply(oldCtx, { projectRoot: '/tmp/pha-checkout' })
const oldSourceRoute = oldRoutes.find((r) => r.path === '/pha/source')
check(!!oldSourceRoute, 'route /pha/source is registered for the old-CLI case too')
if (oldSourceRoute) {
  const oldRes = await fetchJsonRoute(oldSourceRoute, '/pha/source?doc=173')
  check(oldRes.status === 500 && oldRes.body && oldRes.body.ok === false,
    'an old CLI refuses instead of opening something else')
  check(/pha update/.test(String(oldRes.body && oldRes.body.error)),
    'an old CLI gets an actionable "run pha update" message, not the argparse usage')
  check(!oldSpawns.some((s) => s.argv[1] === 'open' && s.argv[2] === '--source'),
    'an old CLI is never asked to run `open --source`')
}
oldPlugin.dispose()

// 4f. Record browsing: `/pha/records` is the browse counterpart of a record search
// (no query, no model call), so the view can list encoder output one page at a time.
// The handler must pass exactly the CLI's filter flags — limit clamped to the
// view's 100, repeated `field` values kept in order — and echo the `pha records
// --json` payload unchanged (a record is already the shape the client renders).
// `/pha/recordcounts` serves the per-document counts a table badges rows with, and
// `/pha/search?source=…` must forward the whitelisted source and keep a record hit
// record-shaped (record_id/encoder/record_kind), not turn it into a page row.
const RECORD = {
  id: 12, document_id: 7, filename: 'doc7.pdf', collection: 'collections/COLX',
  path: '/tmp/pha-rec-archive/dropbox/collections/COLX/doc7.pdf', page_no: 3,
  encoder: 'letters', kind: 'letter', created_at: '2026-01-02T03:04:05',
  data: { place: 'Malaca', text: 'carta de Malaca' },
}
const RECORD_PAYLOAD = { total: 42, limit: 100, offset: 3, records: [RECORD] }
const recSpawns = []
const recTools = []
const recRoutes = []
const recCtx = {
  subprocess: {
    resolveExecutable: async (cand) => (
      cand === 'pha' ? '/usr/bin/pha' : (/python/.test(cand) ? '/usr/bin/python3' : null)),
    spawn: (spec) => {
      recSpawns.push(spec)
      const isPy = /python/.test(String(spec.argv[0] || ''))
      const isInfo = spec.argv[1] === 'info'
      // The DB helper runs as [python3, '-c', DB_SCRIPT, dbPath, op]: the op is argv[4].
      const dbOp = isPy ? spec.argv[4] : null
      let out = ''
      let lossy = false
      if (isInfo) {
        out = JSON.stringify({ archive_dir: '/tmp/pha-rec-archive', db_path: '/tmp/pha-rec-archive/archive.db', configured: true })
      } else if (isPy && dbOp === 'recordcounts') {
        out = JSON.stringify({ 7: 2, 9: 5 })
      } else if (spec.argv[1] === 'records') {
        out = JSON.stringify(RECORD_PAYLOAD)
        // One document id stands for "output past the stdout cap": the tail is not
        // JSON, so the route must say "lower --limit" instead of returning garbage.
        const di = spec.argv.indexOf('--doc')
        lossy = di >= 0 && spec.argv[di + 1] === '999999'
      } else if (spec.argv[1] === 'search') {
        out = JSON.stringify({ mode: 'keyword', results: [
          { kind: 'record', record_id: 12, document_id: 7, filename: 'doc7.pdf',
            collection: 'collections/COLX', page_no: 3, encoder: 'letters',
            record_kind: 'letter', text: 'carta de Malaca' },
          { kind: 'page', document_id: 8, filename: 'doc8.pdf', collection: '(root)',
            page_no: 1, variant: 'raw', text: 'carta' },
          { kind: 'note', note: 'malaca', title: 'Malaca',
            path: '/tmp/pha-rec-archive/notes/malaca.md', text: 'note body' },
        ] })
      } else {
        out = '{}'
      }
      const reader = (text, isLossy) => ({ readFrom: () => ({ text, nextOffset: text.length, lossy: !!isLossy }) })
      return {
        collected: { stdout: reader(out, lossy), stderr: reader('') },
        done: Promise.resolve({ exitCode: 0, signal: null }),
      }
    },
  },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => {} },
  tools: { register: (def) => { recTools.push(def); return () => {} } },
  inject: (deps, fn) => {
    if (deps.includes('webServer')) {
      fn({
        webServer: { register: (r) => { recRoutes.push(r); return () => {} } },
        get: () => undefined,
        effect: (f) => f(),
      })
    }
  },
}
const recPlugin = mod.apply(recCtx, { projectRoot: '/tmp/pha-checkout' })
const recordsRoute = recRoutes.find((r) => r.path === '/pha/records')
const recordCountsRoute = recRoutes.find((r) => r.path === '/pha/recordcounts')
const searchRoute = recRoutes.find((r) => r.path === '/pha/search')
check(!!recordsRoute && typeof recordsRoute.handler === 'function',
  'route /pha/records is registered with a handler')
check(!!recordCountsRoute && typeof recordCountsRoute.handler === 'function',
  'route /pha/recordcounts is registered with a handler')

if (recordsRoute) {
  const before = recSpawns.length
  const res = await fetchJsonRoute(recordsRoute,
    '/pha/records?doc=7&encoder=letters&kind=letter&field=place%3DMalaca&ignore_case=1&limit=500&offset=3')
  check(res.status === 200 && res.body && res.body.ok === true, '/pha/records answers ok')
  const argv = recSpawns.slice(before).map((s) => s.argv).find((a) => a[1] === 'records')
  check(!!argv && JSON.stringify(argv) === JSON.stringify([
    '/usr/bin/pha', 'records', '--json', '--limit', '100', '--offset', '3',
    '--doc', '7', '--encoder', 'letters', '--kind', 'letter',
    '--field', 'place=Malaca', '--ignore-case',
  ]), '/pha/records passes the CLI filters in order, with limit clamped to 100')
  check(res.body && res.body.total === 42 && res.body.limit === 100 && res.body.offset === 3
    && JSON.stringify(res.body.records) === JSON.stringify(RECORD_PAYLOAD.records),
    '/pha/records echoes the `pha records --json` payload unchanged')

  const beforeDefaults = recSpawns.length
  const def = await fetchJsonRoute(recordsRoute, '/pha/records')
  check(def.status === 200 && def.body && def.body.ok === true, '/pha/records answers ok with no filters')
  const defArgv = recSpawns.slice(beforeDefaults).map((s) => s.argv).find((a) => a[1] === 'records')
  check(!!defArgv && JSON.stringify(defArgv.slice(1)) === JSON.stringify([
    'records', '--json', '--limit', '25', '--offset', '0',
  ]), '/pha/records defaults to limit 25, offset 0, no filters')

  const beforeFields = recSpawns.length
  await fetchJsonRoute(recordsRoute, '/pha/records?field=a%3D1&field=b%3D2&ignore_case=0')
  const fieldsArgv = recSpawns.slice(beforeFields).map((s) => s.argv).find((a) => a[1] === 'records')
  check(!!fieldsArgv && JSON.stringify(fieldsArgv.slice(1)) === JSON.stringify([
    'records', '--json', '--limit', '25', '--offset', '0', '--field', 'a=1', '--field', 'b=2',
  ]), '/pha/records keeps every repeated `field` and treats ignore_case=0 as off')

  const lossy = await fetchJsonRoute(recordsRoute, '/pha/records?doc=999999')
  check(lossy.status === 500 && lossy.body && lossy.body.ok === false
    && /--limit/.test(String(lossy.body.error || '')),
    '/pha/records names `--limit` when the CLI output was truncated (lossy)')
}
if (recordCountsRoute) {
  const before = recSpawns.length
  const counts = await fetchJsonRoute(recordCountsRoute, '/pha/recordcounts')
  check(counts.status === 200 && counts.body && counts.body.ok === true, '/pha/recordcounts answers ok')
  check(counts.body && JSON.stringify(counts.body.counts) === JSON.stringify({ 7: 2, 9: 5 }),
    '/pha/recordcounts serves {document_id: count} from the database')
  const dbArgv = recSpawns.slice(before).map((s) => s.argv)
    .find((a) => /python/.test(String(a[0] || '')) && a[4] === 'recordcounts')
  check(!!dbArgv, 'the counts come from the `recordcounts` DB op')
}
if (searchRoute) {
  const before = recSpawns.length
  const res = await fetchJsonRoute(searchRoute, '/pha/search?q=x&source=records')
  const argv = recSpawns.slice(before).map((s) => s.argv).find((a) => a[1] === 'search')
  check(!!argv && argv.indexOf('--source') >= 0 && argv[argv.indexOf('--source') + 1] === 'records',
    '/pha/search passes an explicit `--source records`')
  const hits = (res.body && res.body.results) || []
  const rec = hits.find((r) => r.kind === 'record')
  check(!!rec && rec.record_id === 12 && rec.encoder === 'letters' && rec.record_kind === 'letter'
    && rec.document_id === 7 && rec.page_no === 3,
    '/pha/search keeps a record hit record-shaped (record_id/encoder/record_kind)')
  const page = hits.find((r) => r.kind === 'page')
  check(!!page && page.document_id === 8 && page.page_no === 1, '/pha/search keeps a page hit page-shaped')
  check(hits.every((r) => r.kind !== 'note'), '/pha/search keeps note hits out of the document rows')

  const beforeDefault = recSpawns.length
  await fetchJsonRoute(searchRoute, '/pha/search?q=x')
  const defArgv = recSpawns.slice(beforeDefault).map((s) => s.argv).find((a) => a[1] === 'search')
  check(!!defArgv && defArgv[defArgv.indexOf('--source') + 1] === 'pages',
    '/pha/search defaults to --source pages')
  const beforeUnknown = recSpawns.length
  await fetchJsonRoute(searchRoute, '/pha/search?q=x&source=bogus')
  const unknownArgv = recSpawns.slice(beforeUnknown).map((s) => s.argv).find((a) => a[1] === 'search')
  check(!!unknownArgv && unknownArgv[unknownArgv.indexOf('--source') + 1] === 'pages',
    '/pha/search falls back to pages for an unknown source')
}

// The agent tool must agree with the route: same argv helper, same payload shape.
const recordsTool = recTools.find((t) => t.name === 'pha_records')
check(!!recordsTool && typeof recordsTool.execute === 'function', 'tool pha_records is registered')
if (recordsTool) {
  const before = recSpawns.length
  const out = await recordsTool.execute({ doc: 7, encoder: 'letters', kind: 'letter',
    field: 'place=Malaca', ignore_case: true, limit: 500, offset: 3 })
  const argv = recSpawns.slice(before).map((s) => s.argv).find((a) => a[1] === 'records')
  check(!!argv && JSON.stringify(argv.slice(1)) === JSON.stringify([
    'records', '--json', '--limit', '100', '--offset', '3', '--doc', '7',
    '--encoder', 'letters', '--kind', 'letter', '--field', 'place=Malaca', '--ignore-case',
  ]), 'pha_records builds the same argv as /pha/records (limit clamped to 100)')
  check(out && out.ok === true && out.total === 42 && out.limit === 100 && out.records.length === 1,
    'pha_records returns the parsed `pha records --json` payload with ok:true')
}
recPlugin.dispose()

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
check(bundle.includes('/pha/source'), 'client bundle asks the source-open route')
check(bundle.includes('pha-open-src'), 'client bundle renders the row-level "open" button')
check(bundle.includes('pha-serve-link'), 'client bundle renders the pha serve link under the page image')
check(!/from\s*["']\.\/(links|viewmemory|askcontext|query|statusview)\.js["']/.test(bundle), 'client bundle has no leftover inline-module import')
check(!/^\s*import\s/m.test(bundle), 'client bundle imports nothing')

console.log('')
if (failures.length) {
  console.log(`${failures.length} check(s) FAILED`)
  process.exit(1)
}
console.log(`all checks passed — ${tools.length} tools, ${routes.length} routes, client bundle present`)
