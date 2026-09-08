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
        try {
          const r = await phaRun(['status'])
          const m = String(r.out || '').match(/^archive:\s*(\S+archive\.db)\s*$/m)
          if (m && m[1]) {
            dbPath = m[1]
            archiveDir = dbPath.replace(/archive\.db$/, '').replace(/\/+$/, '')
            return dbPath
          }
        } catch (e) { /* fall through */ }
        const r2 = await phaRun(['doctor', '--json'])
        const mm = String(r2.out || '').match(/\{[\s\S]*\}/)
        if (!mm) throw new Error('cannot discover archive (pha status and doctor both unparsable)')
        const data = JSON.parse(mm[0])
        if (!data || !data.archive) throw new Error('pha reported no archive')
        archiveDir = data.archive
        dbPath = archiveDir + '/archive.db'
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
    ['pha_search', 'Full-text search across the archive (hybrid). Returns structured hits with document id, page number, variant and snippet text.', { query: { type: 'string', description: 'search terms' }, limit: { type: 'integer', description: 'max hits (default 5, max 20)' } }, ['query'], async (a) => {
      const q = String(a.query || '').trim()
      if (!q) throw new Error('query required')
      const limit = Math.min(20, Math.max(1, Number(a.limit) || 5))
      const r = await phaRun(['search', q, '--json', '--limit', String(limit)])
      if (r.code !== 0) throw new Error('pha search failed: ' + String(r.err || r.out).slice(0, 400))
      const data = parseJson(r.out)
      const results = (data.results || []).map((hit) => ({
        document_id: hit.document_id, filename: hit.filename, collection: hit.collection,
        page_no: hit.page_no, variant: hit.variant, mode: data.mode || 'hybrid',
        text: String(hit.text || '').slice(0, 900),
      }))
      return { ok: true, query: q, mode: data.mode || 'hybrid', results }
    }],
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
    ['/pha/search', json(async (p) => {
      const q = String(p.q || '').trim()
      if (!q) throw new Error('query required')
      const limit = Math.min(20, Math.max(1, Number(p.limit) || 5))
      const r = await phaRun(['search', q, '--json', '--limit', String(limit)])
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
    ['/pha/status', json(async () => ({ ok: true, text: String((await phaRun(['status'])).out || '') }))],
    ['/pha/archive', json(async () => ({ ok: true, archive: await discover().then(() => archiveDir) }))],
  ]
  for (const [path, handler] of routes) {
    const ws = ctx.get('webServer')
    if (!ws) break
    ctx.effect(() => ws.register({ kind: 'exact', path, handler }))
  }

  return { dispose() { jobs.forEach((rec) => { if (rec.handle && rec.state === 'running') { try { rec.handle.terminate() } catch (e) {} } }) } }
}

export { apply, inject, name }
