import { defineTool } from '@deepseek-ai/dsh-tools'

const name = 'dsh-pha'
const inject = ['subprocess', 'tools']

const MAX = 4 * 1024 * 1024
const JOB_MAX = 16 * 1024 * 1024

const DB_SCRIPT = [
  'import sqlite3, json, sys',
  'db = sys.argv[1]',
  'op = sys.argv[2]',
  'c = sqlite3.connect("file:" + db + "?immutable=1", uri=True)',
  'c.row_factory = sqlite3.Row',
  'q = c.cursor()',
  'def out(obj):',
  '    print(json.dumps(obj, ensure_ascii=False))',
  '    raise SystemExit(0)',
  'if op == "documents":',
  '    rows = q.execute("select id, filename, dir_path, kind, page_count, status, palaeographer, editor, palaeographer_model, editor_model, error from documents order by id").fetchall()',
  '    out([dict(r) for r in rows])',
  'if op == "document":',
  '    ident = sys.argv[3]',
  '    doc = q.execute("select id, filename, path, dir_path, kind, page_count, status, palaeographer, editor, palaeographer_model, editor_model, error, updated_at from documents where id = ? or filename = ? or path = ? limit 1", (ident, ident, ident)).fetchone()',
  '    if doc is None:',
  '        out(None)',
  '    d = dict(doc)',
  '    pages = q.execute("select id, page_no, status, error, reviewed_at, exported_at from pages where document_id = ? order by page_no", (d["id"],)).fetchall()',
  '    edits = q.execute("select p.page_no, pe.editor, pe.status, pe.error, pe.reviewed_at from page_edits pe join pages p on p.id = pe.page_id where p.document_id = ? order by p.page_no, pe.editor", (d["id"],)).fetchall()',
  '    out({"doc": d, "pages": [dict(r) for r in pages], "edits": [dict(r) for r in edits]})',
  'if op == "pagemeta":',
  '    pid = int(sys.argv[3]); pno = int(sys.argv[4])',
  '    r = q.execute("select d.sha256 as sha256, p.source_name as source_name from documents d left join pages p on p.document_id = d.id and p.page_no = ? where d.id = ?", (pno, pid)).fetchone()',
  '    out(dict(r) if r else None)',
  'out({"error": "unknown op: " + op})',
].join('\n')

const LIST_NOTES = "import os,sys,json\nd=sys.argv[1]\nout=[]\nfor f in sorted(os.listdir(d)) if os.path.isdir(d) else []:\n    if f.endswith('.md') and f.lower() != 'readme.md':\n        out.append({'name': f[:-3], 'file': f})\nprint(json.dumps(out, ensure_ascii=False))"
const READ_NOTE = "import sys\nsys.stdout.write(open(sys.argv[1], encoding='utf-8').read())"

function apply(ctx) {
  const cwd = '/'
  const jobs = new Map()
  let jobSeq = 1
  let phaBin = null
  let pyBin = null
  let archiveDir = null
  let dbPath = null
  let discoverPromise = null

  async function firstResolvable(cands) {
    for (const cand of cands) {
      try {
        const r = await ctx.subprocess.resolveExecutable(cand)
        if (r) return r
      } catch (e) { /* try next */ }
    }
    return null
  }
  async function pha() {
    if (phaBin === null) {
      phaBin = await firstResolvable(['pha', '/Users/jrc/.local/bin/pha', '/usr/local/bin/pha', '/opt/homebrew/bin/pha'])
    }
    if (!phaBin) throw new Error('pha executable not found (tried PATH and common install dirs)')
    return phaBin
  }
  async function python() {
    if (pyBin === null) {
      pyBin = await firstResolvable(['python3', '/usr/bin/python3', '/opt/homebrew/bin/python3'])
    }
    if (!pyBin) throw new Error('python3 not found')
    return pyBin
  }

  function spawnOut(argv, opts) {
    const outMax = (opts && opts.outMax) || MAX
    const errMax = (opts && opts.errMax) || MAX
    return new Promise((resolve, reject) => {
      let handle
      try {
        handle = ctx.subprocess.spawn({
          argv,
          cwd,
          graceMs: 1500,
          stdio: {
            stdin: 'ignore',
            stdout: { maxBytes: outMax, spill: { maxBytes: outMax * 4 } },
            stderr: { maxBytes: errMax, spill: { maxBytes: errMax * 4 } },
          },
        })
      } catch (e) { reject(e); return }
      handle.done.then(
        (o) => {
          const out = handle.collected.stdout ? handle.collected.stdout.readFrom(0).text : ''
          const err = handle.collected.stderr ? handle.collected.stderr.readFrom(0).text : ''
          resolve({ code: o.exitCode, signal: o.signal, out, err })
        },
        (e) => reject(e),
      )
    })
  }
  async function phaRun(args, opts) { return spawnOut([await pha(), ...args], opts) }
  async function pyRun(code, pyArgs) { return spawnOut([await python(), '-c', code, ...(pyArgs || [])]) }

  function discover() {
    if (dbPath) return Promise.resolve(dbPath)
    if (!discoverPromise) {
      discoverPromise = (async () => {
        // 1) cheapest and always correct: `pha info` reads only the config (~3s).
        //    This must come first — the view's initial load waits on discovery, and
        //    `pha status` (below) walks every library page file.
        try {
          const r = await phaRun(['info', '--json'])
          const data = parseJson(r.out)
          if (data && data.archive_dir) {
            archiveDir = String(data.archive_dir).replace(/\/+$/, '')
            dbPath = data.db_path ? String(data.db_path) : archiveDir + '/archive.db'
            return dbPath
          }
        } catch (e) { /* fall through to the slower probes */ }
        // 2) `pha doctor --json` reports the archive but probes the engine binaries
        //    (tens of seconds when it queries the login-shell PATH).
        try {
          const r2 = await phaRun(['doctor', '--json'])
          const mm = String(r2.out || '').match(/\{[\s\S]*\}/)
          if (mm) {
            const data = JSON.parse(mm[0])
            if (data && data.archive) {
              archiveDir = data.archive
              dbPath = archiveDir + '/archive.db'
              return dbPath
            }
          }
        } catch (e) { /* fall through */ }
        // 3) last resort: `pha status` (slowest) — its first line names the db.
        const r3 = await phaRun(['status'])
        const m = String(r3.out || '').match(/^archive:\s*(\S+archive\.db)\s*$/m)
        if (!m || !m[1]) throw new Error('cannot discover archive')
        dbPath = m[1]
        archiveDir = dbPath.replace(/archive\.db$/, '').replace(/\/+$/, '')
        return dbPath
      })().catch((e) => { discoverPromise = null; throw e })
    }
    return discoverPromise
  }

  async function runDb(op, extra) {
    await discover()
    const r = await pyRun(DB_SCRIPT, [dbPath, op, ...(extra || [])])
    if (r.code !== 0) throw new Error('db read failed: ' + String(r.err || r.out).slice(0, 500))
    return JSON.parse(String(r.out).trim())
  }

  function parseJson(text) {
    const t = String(text).trim()
    try { return JSON.parse(t) } catch (e) { /* fall through */ }
    const m = t.match(/\{[\s\S]*\}/)
    if (!m) throw new Error('unparsable pha output: ' + t.slice(0, 300))
    return JSON.parse(m[0])
  }

  const ACTIONS = ['scan', 'edit', 'encode', 'reindex', 'review', 'inbox']
  const PATH_RE = /^[\w./ -]+$/
  function buildArgv(action, a) {
    const argv = [action]
    if (a.path && PATH_RE.test(a.path) && a.path.length <= 300) argv.push('--path', a.path)
    if (a.doc && String(a.doc).length <= 20 && /^\d+$/.test(String(a.doc))) argv.push('--doc', String(a.doc))
    if (a.page && /^\d+$/.test(String(a.page)) && Number(a.page) < 100000) argv.push('--page', String(a.page))
    if (a.reprocess) argv.push('--reprocess')
    return argv
  }

  async function startJobAction(action, a) {
    if (ACTIONS.indexOf(action) < 0) throw new Error('action not allowed: ' + action)
    const argv = buildArgv(action, a || {})
    const bin = await pha()
    const id = 'pha-' + (jobSeq++)
    const rec = { id, action, argv, state: 'running', code: null, requestedKill: false, outC: 0, errC: 0, truncated: false, error: null, handle: null }
    const handle = ctx.subprocess.spawn({
      argv: [bin, ...argv],
      cwd,
      graceMs: 2000,
      stdio: {
        stdin: 'ignore',
        stdout: { maxBytes: JOB_MAX, spill: { maxBytes: JOB_MAX * 2 } },
        stderr: { maxBytes: JOB_MAX, spill: { maxBytes: JOB_MAX * 2 } },
      },
    })
    rec.handle = handle
    jobs.set(id, rec)
    handle.done.then(
      (o) => {
        rec.code = o.exitCode
        if (rec.requestedKill) rec.state = 'killed'
        else rec.state = o.exitCode === 0 ? 'done' : 'error'
      },
      (e) => { rec.state = 'error'; rec.error = String((e && e.message) || e) },
    )
    return { id, argv }
  }

  function pull(reader, from) {
    if (!reader) return { text: '', at: from || 0, lossy: false }
    const r = reader.readFrom(from || 0)
    return { text: r.text || '', at: r.nextOffset, lossy: !!r.lossy }
  }

  function jobStatus(id) {
    const rec = jobs.get(String(id))
    if (!rec) return { ok: false, error: 'no such job: ' + id }
    const out = pull(rec.handle && rec.handle.collected.stdout, rec.outC)
    const err = pull(rec.handle && rec.handle.collected.stderr, rec.errC)
    rec.outC = out.at
    rec.errC = err.at
    if (out.lossy || err.lossy) rec.truncated = true
    const done = rec.state === 'done' || rec.state === 'error' || rec.state === 'killed'
    return { ok: true, id: rec.id, state: rec.state, exitCode: rec.code, done, deltaOut: out.text, deltaErr: err.text, truncated: rec.truncated, error: rec.error }
  }

  function textRender(_args, value) {
    let text
    if (typeof value === 'string') text = value
    else {
      try { text = JSON.stringify(value, null, 2) } catch (e) { text = String(value) }
    }
    if (text.length > 200000) text = text.slice(0, 200000) + '\n…(output truncated)'
    return [{ type: 'text', text }]
  }

  function mkTool(tname, description, props, required, run) {
    const def = defineTool({
      name: tname,
      description,
      parameters: { type: 'object', properties: props, required: required || [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: textRender },
      execute: run,
    })
    ctx.effect(() => ctx.tools.register(def))
  }

  // Open a page's library file in the OS-default app via `pha open <doc> <page>`.
  // pha resolves doc/page to the .md under the archive and validates it, so the
  // caller never hands us an arbitrary path. A definition file (model /
  // palaeographer / editor) is opened with the explicit-path form, which pha also
  // validates (must be an existing .md inside the archive).
  async function runOpen(a) {
    const argv = ['open']
    if (a.path) {
      argv.push(String(a.path))
    } else {
      if (!a.doc || !a.page) throw new Error('doc and page required (or path)')
      argv.push(String(a.doc), String(a.page))
      if (a.edited) argv.push('--edited')
    }
    const r = await phaRun(argv)
    if (r.code !== 0) throw new Error('pha open failed: ' + String(r.err || r.out).slice(0, 400))
    const out = String(r.out || '').trim()
    const m = out.match(/^opened:\s*(.+)$/m)
    return { ok: true, output: out, path: m ? m[1].trim() : null }
  }

  // Definition folders at the archive root, browsable + editable in the view.
  // listed palaeographers -> editors -> models (the reading rules, then the models
  // they pair with); the order is what the view's left pane shows
  const DEF_KINDS = ['palaeographers', 'editors', 'encoders', 'models']
  const LIST_DEFS = [
    'import os,sys,json',
    'root=sys.argv[1]; kinds=json.loads(sys.argv[2])',
    'out=[]',
    'for k in kinds:',
    '    d=os.path.join(root,k)',
    '    if not os.path.isdir(d): continue',
    '    for f in sorted(os.listdir(d)):',
    "        if f.endswith('.md'):",
    "            out.append({'kind':k,'name':f[:-3],'file':f,'path':os.path.join(d,f)})",
    'print(json.dumps(out, ensure_ascii=False))',
  ].join('\n')
  function defPathAllowed(p) {
    if (!p.endsWith('.md')) return false
    return DEF_KINDS.some((k) => p.startsWith(archiveDir + '/' + k + '/'))
  }

  // Report library page files a human edited but that are not yet imported into
  // the DB for one document (`pha pending --doc N`) — the DB is out of sync with
  // the library files on disk. Cached briefly: the check stats every page file of
  // the document, so the view must not re-run it on every render.
  const pendingCache = new Map()
  async function runPending(doc) {
    const key = String(doc)
    const hit = pendingCache.get(key)
    const now = Date.now()
    if (hit && (now - hit.at) < 30000) return hit.data
    const r = await phaRun(['pending', '--doc', key, '--json'])
    if (r.code !== 0) throw new Error('pha pending failed: ' + String(r.err || r.out).slice(0, 400))
    const data = parseJson(r.out)
    const pages = (data.pending || []).map((x) => ({
      document_id: x.document_id, page_no: x.page_no, variant: x.variant, editor: x.editor || null,
    }))
    const out = {
      ok: true,
      count: typeof data.count === 'number' ? data.count : pages.length,
      needs: data.needs || {
        review: pages.length > 0,
        edit: pages.some((x) => String(x.variant || '').startsWith('transcription-')),
        reindex: pages.length > 0,
      },
      pending: pages,
    }
    pendingCache.set(key, { at: now, data: out })
    return out
  }

  // Resolve how a document/collection is processed and return its pha.yaml.
  // `--write` generates the pha.yaml from the resolved configuration when the
  // collection has none on its chain (idempotent: an existing pha.yaml is shown,
  // never rewritten), so the view can surface a collection still configured only
  // by the legacy palaeographer/editor selection files.
  async function runConfig(doc) {
    const r = await phaRun(['config', '--doc', String(doc), '--json', '--write'])
    if (r.code !== 0) throw new Error('pha config failed: ' + String(r.err || r.out).slice(0, 400))
    const d = parseJson(r.out)
    return {
      ok: true,
      target: d.target || null,
      path: d.path || null,
      document_dir: d.document_dir || null,
      // true when the pha.yaml shown is inherited from an upper folder (this
      // directory has none of its own — and none is created for it)
      inherited: !!d.inherited,
      generated: !!d.generated,
      legacy_files: d.legacy_files || [],
      resolved: d.resolved || null,
      problems: (d.resolved && d.resolved.problems) || [],
      content: d.content || null,
    }
  }

  const tools = [
    ['pha_status', 'Run `pha status` on the configured archive and return its text report (documents, pages, chunks, new and on-hold files).', {}, [], async () => {
      const r = await phaRun(['status'])
      return { ok: true, archive: archiveDir || null, text: String(r.out || r.err || '').slice(0, 20000) }
    }],
    ['pha_documents', 'List all documents in the archive as structured JSON (id, filename, dir_path, kind, page_count, status, palaeographer, editor, models, error).', {}, [], async () => ({ ok: true, archive: archiveDir, documents: await runDb('documents') })],
    ['pha_document', 'Get one document (by id, filename or dropbox path) with its pages and per-page editor entries as structured JSON.', { doc: { type: 'string', description: 'document id, filename substring or dropbox-relative path' } }, ['doc'], async (a) => {
      const r = await runDb('document', [String(a.doc)])
      if (r === null) return { ok: true, doc: null }
      return { ok: true, doc: r.doc, pages: r.pages, edits: r.edits }
    }],
    ['pha_page', 'Read one page of a document as structured JSON (raw transcription by default; pass edited=true for the edited variant).', { doc: { type: 'string', description: 'document id or filename substring' }, page: { type: 'integer', description: 'page number (1-based)' }, edited: { type: 'boolean', description: 'read the edited variant instead of raw' } }, ['doc', 'page'], async (a) => {
      const argv = ['page', String(a.doc), String(a.page), '--json']
      if (a.edited) argv.push('--edited')
      const r = await phaRun(argv)
      if (r.code !== 0) throw new Error('pha page failed: ' + String(r.err || r.out).slice(0, 400))
      return { ok: true, page: parseJson(r.out) }
    }],
    ['pha_search', 'Full-text search across the archive (keyword / phrase match). Returns structured hits with document id, page number, variant and snippet text — only pages that actually contain the query terms.', { query: { type: 'string', description: 'search terms' }, limit: { type: 'integer', description: 'max hits (default 5, max 20)' } }, ['query'], async (a) => {
      const q = String(a.query || '').trim()
      if (!q) throw new Error('query required')
      const limit = Math.min(20, Math.max(1, Number(a.limit) || 5))
      const r = await phaRun(['search', q, '--mode', 'keyword', '--json', '--limit', String(limit)])
      if (r.code !== 0) throw new Error('pha search failed: ' + String(r.err || r.out).slice(0, 400))
      const data = parseJson(r.out)
      const results = (data.results || []).map((hit) => ({
        document_id: hit.document_id, filename: hit.filename, collection: hit.collection,
        page_no: hit.page_no, variant: hit.variant, mode: data.mode || 'keyword',
        text: String(hit.text || '').slice(0, 900),
      }))
      return { ok: true, query: q, mode: data.mode || 'keyword', results }
    }],
    ['pha_open', 'Open one page of a document in the OS-default application (e.g. a markdown editor) so a human can review/edit the library file on the archive machine. The OS decides which app handles the .md; pha never edits the text (a human edits, then pha review imports).', { doc: { type: 'string', description: 'document id or filename substring' }, page: { type: 'integer', description: 'page number (1-based)' }, edited: { type: 'boolean', description: 'open the edited variant instead of raw' } }, ['doc', 'page'], async (a) => runOpen(a)],
    ['pha_pending', 'List the library page files a human edited but that are not yet imported into the DB for one document (numeric document id) — i.e. the DB is out of sync with the library files on disk. Returns the pages plus which pha passes (review / edit / reindex) are needed to bring it back in sync.', { doc: { type: 'string', description: 'document id (number)' } }, ['doc'], async (a) => runPending(a.doc)],
    ['pha_archive', 'Show the configured archive directory and engine health (pha doctor). Broken optional engines are reported as data, not an error.', {}, [], async () => {
      const r = await phaRun(['doctor', '--json'])
      let data
      try { data = parseJson(r.out || r.err || '') } catch (e) {
        return { ok: true, archive: archiveDir, healthy: false, broken: [], engines: [] }
      }
      const engines = (data.engines || []).map((e) => ({ engine: e.engine, ok: !!e.ok, path: e.path || null, version: e.version || null }))
      return { ok: true, archive: data.archive || archiveDir, healthy: !!data.ok, broken: data.broken || [], engines }
    }],
    ['pha_job_start', 'Start a pha job in the background and return its job id: action scan|edit|encode|reindex|review|inbox with optional path/doc/page/reprocess. Poll with pha_job_status.', { action: { type: 'string', enum: ['scan', 'edit', 'encode', 'reindex', 'review', 'inbox'], description: 'pha command to run' }, path: { type: 'string', description: 'dropbox-relative path to restrict to' }, doc: { type: 'string', description: 'document id (review only)' }, reprocess: { type: 'boolean', description: 'add --reprocess' } }, ['action'], async (a) => {
      const job = await startJobAction(String(a.action || ''), a)
      return { ok: true, job_id: job.id, command: 'pha ' + job.argv.join(' ') }
    }],
    ['pha_job_status', 'Poll a background pha job started with pha_job_start. Returns its state and output accumulated since the previous poll.', { id: { type: 'string', description: 'job id from pha_job_start' } }, ['id'], async (a) => jobStatus(a.id)],
    ['pha_job_kill', 'Terminate a background pha job.', { id: { type: 'string' } }, ['id'], async (a) => {
      const rec = jobs.get(String(a.id))
      if (!rec) return { ok: false, error: 'no such job' }
      rec.requestedKill = true
      if (rec.handle) rec.handle.terminate()
      return { ok: true }
    }],
  ]

  for (const [tname, description, props, required, run] of tools) {
    try {
      mkTool(tname, description, props, required, run)
    } catch (e) {
      console.error('[dsh-pha] tool registration failed for ' + tname + ': ' + String((e && e.message) || e))
    }
  }

  // ---- durable browser data layer (same-origin /pha/* JSON routes) --------
  // Read-only: browsing, page text + render image, search. Mutations go through
  // the pha_* tools / the agent, so do the pha lock + staleness semantics.
  // Two exceptions: `/pha/open` launches the OS-default app on a page file
  // (doc/page resolved + validated by `pha open`), and `/pha/config` may write a
  // collection's pha.yaml from the resolved configuration (only when it has none).
  function json(handler) {
    return (req, res) => {
      (async () => {
        try {
          const params = Object.fromEntries(new URL(req.url, 'http://localhost').searchParams)
          const data = await handler(params)
          res.writeHead(200, { 'content-type': 'application/json' })
          res.end(JSON.stringify(data))
        } catch (e) {
          res.writeHead(500, { 'content-type': 'application/json' })
          res.end(JSON.stringify({ ok: false, error: String((e && e.message) || e) }))
        }
      })()
    }
  }

  async function pageImage(params) {
    if (!params.doc || !params.page) throw new Error('doc and page required')
    await discover()
    const meta = await runDb('pagemeta', [String(params.doc), String(params.page)])
    if (!meta || !meta.sha256) return { ok: false, error: 'no render metadata for this page' }
    const dir = archiveDir + '/renders/' + meta.sha256
    const cands = []
    if (meta.source_name) cands.push(meta.source_name + '.jpg', meta.source_name + '.jpeg')
    cands.push('p' + String(params.page).padStart(3, '0') + '.jpg')
    const code = [
      'import base64,sys,os,json',
      'dirp=sys.argv[1]; cands=json.loads(sys.argv[2])',
      'path=None',
      'for f in cands:',
      '    p=os.path.join(dirp,f)',
      '    if os.path.isfile(p): path=p; break',
      'if path is None:',
      '    print(""); raise SystemExit(1)',
      'sys.stdout.buffer.write(b"data:image/jpeg;base64," + base64.b64encode(open(path,"rb").read()))',
    ].join('\n')
    const r = await pyRun(code, [dir, JSON.stringify(cands)])
    if (r.code !== 0 || !r.out) return { ok: false, exists: false, error: 'render not found for this page' }
    return { ok: true, dataUrl: String(r.out).trim(), path: dir }
  }

  const routes = [
    ['/pha/documents', json(async () => ({ ok: true, archive: archiveDir, documents: await runDb('documents') }))],
    ['/pha/document', json(async (p) => {
      if (!p.doc) throw new Error('doc required')
      const r = await runDb('document', [String(p.doc)])
      if (r === null) return { ok: true, doc: null, pages: [], edits: [] }
      return { ok: true, doc: r.doc, pages: r.pages, edits: r.edits }
    })],
    ['/pha/page', json(async (p) => {
      if (!p.doc || !p.page) throw new Error('doc and page required')
      const argv = ['page', String(p.doc), String(p.page), '--json']
      if (p.edited === '1' || p.edited === 'true') argv.push('--edited')
      const r = await phaRun(argv)
      if (r.code !== 0) throw new Error('pha page failed: ' + String(r.err || r.out).slice(0, 400))
      return { ok: true, page: parseJson(r.out) }
    })],
    ['/pha/open', json(async (p) => {
      if (p.path) return await runOpen({ path: p.path })
      if (!p.doc || !p.page) throw new Error('doc and page required (or path)')
      return await runOpen({ doc: p.doc, page: p.page, edited: (p.edited === '1' || p.edited === 'true') })
    })],
    ['/pha/defs', json(async () => {
      await discover()
      const r = await pyRun(LIST_DEFS, [archiveDir, JSON.stringify(DEF_KINDS)])
      if (r.code !== 0) return { ok: true, defs: [] }
      let defs
      try { defs = JSON.parse(String(r.out).trim()) } catch (e) { defs = [] }
      return { ok: true, archive: archiveDir, defs }
    })],
    ['/pha/def', json(async (p) => {
      if (!p.path) throw new Error('path required')
      await discover()
      const path = String(p.path)
      // only the archive's own definition folders (never an arbitrary path)
      if (!defPathAllowed(path)) throw new Error('not a model/palaeographer/editor .md in this archive')
      const r = await pyRun(READ_NOTE, [path])
      if (r.code !== 0) throw new Error('definition file not found')
      const name = path.split('/').pop().replace(/\.md$/, '')
      const kind = DEF_KINDS.find((k) => path.startsWith(archiveDir + '/' + k + '/')) || null
      return { ok: true, path, name, kind, content: String(r.out) }
    })],
    ['/pha/pending', json(async (p) => {
      if (!p.doc) throw new Error('doc required')
      return await runPending(p.doc)
    })],
    ['/pha/config', json(async (p) => {
      if (!p.doc) throw new Error('doc required')
      return await runConfig(p.doc)
    })],
    ['/pha/search', json(async (p) => {
      const q = String(p.q || '').trim()
      if (!q) throw new Error('query required')
      const limit = Math.min(20, Math.max(1, Number(p.limit) || 5))
      const r = await phaRun(['search', q, '--mode', 'keyword', '--json', '--limit', String(limit)])
      if (r.code !== 0) throw new Error('pha search failed: ' + String(r.err || r.out).slice(0, 400))
      const data = parseJson(r.out)
      const results = (data.results || []).map((hit) => ({
        document_id: hit.document_id, filename: hit.filename, collection: hit.collection,
        page_no: hit.page_no, variant: hit.variant,
        text: String(hit.text || '').slice(0, 900),
      }))
      return { ok: true, query: q, results }
    })],
    ['/pha/pageImage', json(pageImage)],
    ['/pha/notes', json(async () => {
      await discover()
      const r = await pyRun(LIST_NOTES, [archiveDir + '/notes'])
      if (r.code !== 0) return { ok: true, notes: [] }
      let notes
      try { notes = JSON.parse(String(r.out).trim()) } catch (e) { notes = [] }
      return { ok: true, notes }
    })],
    ['/pha/note', json(async (p) => {
      if (!p.name) throw new Error('name required')
      const name = String(p.name)
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name)) throw new Error('invalid note name')
      await discover()
      const path = archiveDir + '/notes/' + name + '.md'
      const r = await pyRun(READ_NOTE, [path])
      if (r.code !== 0) throw new Error('note not found')
      return { ok: true, name, path, content: String(r.out) }
    })],
    ['/pha/status', json(async () => ({ ok: true, text: String((await phaRun(['status'])).out || '') }))],
    ['/pha/archive', json(async () => ({ ok: true, archive: await discover().then(() => archiveDir) }))],
  ]
  // Register the routes once a webserver exists. `ctx.inject` (not this plugin's
  // inject list) is deliberate: a profile without a webserver must still get the
  // pha_* tools, so the whole plugin must not wait on webServer. The old
  // ctx.get('webServer') + break silently registered nothing when the service
  // was not up yet at apply time.
  ctx.inject(['webServer'], (wsCtx) => {
    const ws = wsCtx.webServer || wsCtx.get('webServer')
    if (!ws) return
    for (const [path, handler] of routes) {
      wsCtx.effect(() => ws.register({ kind: 'exact', path, handler }))
    }
  })

  return { dispose() { jobs.forEach((rec) => { if (rec.handle && rec.state === 'running') { try { rec.handle.terminate() } catch (e) {} } }) } }
}

export { apply, inject, name }
