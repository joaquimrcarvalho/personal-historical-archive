// dsh-pha client module — the PHA conversation view.
//
// SOURCE. The deployment's `dev:web` client build bundles this into `lib/client.js`
// (discovered via the package.json `dsh.client` block). Data comes from the same-origin
// `/pha/*` JSON endpoints registered by the host half (lib/index.js).
import React from 'react'
import { classifyLink, docSlugFor, parseWikilink, resolveNoteName, slugifyPath } from './links.js'

async function get(path) {
  const res = await fetch(path)
  return await res.json()
}

// ---- compact markdown -> React (headings, lists, tables, footnotes w/ caret, wikilinks)
function inline(t, o) {
  const out = []
  // Normalize LaTeX-style \\(^{n}\\) / \\(_{n}\\) to $^{n}$ / $_{n}$ so they render as sup/sub.
  let s = String(t == null ? '' : t)
  s = s.split('\\(').join('$')
  s = s.split('\\)').join('$')
  t = s
  const re = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`|~~[^~\n]+~~|\[\[([^\]]+)\]\]|\[\^(\d+)\]|\[([^\]]+)\]\(([^)]+)\)|\$[\^_]\{[^}]*\}\$)/g
  let last = 0
  let m
  while ((m = re.exec(t))) {
    if (m.index > last) out.push(t.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**') && tok.endsWith('**')) out.push(React.createElement('strong', null, tok.slice(2, -2)))
    else if (tok.startsWith('__') && tok.endsWith('__')) out.push(React.createElement('strong', null, tok.slice(2, -2)))
    else if (tok.startsWith('`') && tok.endsWith('`')) out.push(React.createElement('code', null, tok.slice(1, -1)))
    else if (tok.startsWith('~~') && tok.endsWith('~~')) out.push(React.createElement('del', null, tok.slice(2, -2)))
    else if (tok.startsWith('[[')) {
      // [[Note]] · [[Note|label]] · [[Note#Heading]] · [[Note.md]] — resolved against
      // the note list when we have it, so a miss renders as a dangling link instead of
      // failing silently on click.
      const w = parseWikilink(tok.slice(2, -2))
      const known = o.noteNames
      const resolved = known && known.length ? resolveNoteName(w.target, known) : null
      const dangling = !!(known && known.length && !resolved)
      const target = resolved || w.target
      const title = (dangling ? 'no note named ' + w.target + ' — click to look for it' : 'note: ' + target)
        + (w.anchor ? ' #' + w.anchor : '')
      // Outside the notes viewer (a definition body, a page's edited text) there is no
      // note context to open: show the wikilink as text rather than a dead link.
      out.push(o.onNote
        ? React.createElement('a', {
            className: 'pha-note-link' + (dangling ? ' dangling' : ''),
            title,
            key: 'wl' + m.index,
            onClick: (ev) => { if (ev && ev.preventDefault) ev.preventDefault(); o.onNote(target, w.anchor) },
          }, w.label)
        : React.createElement('span', { className: 'pha-note-link', title, key: 'wl' + m.index }, w.label))
    } else if (tok.startsWith('[^')) {
      const n = tok.slice(2, -1)
      out.push(React.createElement('sup', { id: 'fnref-' + n, key: 'fnr' + n },
        React.createElement('a', {
          href: '#fn-' + n,
          onClick: (ev) => { if (o.onAnchor) { ev.preventDefault(); o.onAnchor('fn-' + n) } },
        }, n)))
    } else if (tok.startsWith('[')) {
      const nm = tok.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
      if (!nm) out.push(tok)
      else {
        const label = nm[1]
        const href = nm[2].trim()
        const c = classifyLink(href)
        const key = 'md' + m.index
        if (c.kind === 'anchor') {
          // Same-page jumps never leave the harness, with or without a scroll handler.
          out.push(React.createElement('a', {
            href,
            key,
            onClick: o.onAnchor ? (ev) => { ev.preventDefault(); o.onAnchor(c.id) } : undefined,
          }, label))
        } else if (c.kind === 'citation' && o.onCitation) {
          // A `pha cite` footnote: open the page in THIS view (image + text) instead of
          // leaving the harness for the pha serve viewer.
          out.push(React.createElement('a', {
            className: 'pha-page-link',
            href,
            key,
            title: 'open p.' + (c.page === null ? '?' : c.page) + ' of ' + c.slug + ' in this view',
            onClick: (ev) => { ev.preventDefault(); o.onCitation(c) },
          }, label, c.page === null ? null : React.createElement('span', { className: 'pha-link-tag' }, 'p.' + c.page)))
        } else if (c.kind === 'note' && o.onNote) {
          out.push(React.createElement('a', {
            className: 'pha-note-link',
            href,
            key,
            title: 'note: ' + c.name,
            onClick: (ev) => { ev.preventDefault(); o.onNote(c.name, null) },
          }, label))
        } else {
          // Everything else leaves the app: a new tab keeps the harness page — and this
          // conversation — exactly where it is.
          out.push(React.createElement('a', {
            className: 'pha-ext-link',
            href: c.href,
            target: '_blank',
            rel: 'noopener noreferrer',
            title: c.href,
            key,
          }, label, React.createElement('span', { className: 'pha-ext-mark' }, '↗')))
        }
      }
    } else if (tok.startsWith('$')) {
      const sc = tok.match(/^\$([\^_])\{(.*)\}\$$/)
      out.push(sc ? React.createElement(sc[1] === '^' ? 'sup' : 'sub', null, sc[2]) : tok)
    } else if ((tok.startsWith('*') && tok.endsWith('*')) || (tok.startsWith('_') && tok.endsWith('_'))) {
      out.push(React.createElement('em', null, tok.slice(1, -1)))
    } else out.push(tok)
    last = re.lastIndex
  }
  if (last < t.length) out.push(t.slice(last))
  return out
}

function renderMd(text, opts) {
  const o = opts || {}
  if (o.renderFootnotes === undefined) o.renderFootnotes = true
  if (o.footnotes === undefined) o.footnotes = {}
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks = []
  const headingIds = {}
  // Headings carry a stable id so `[[Note#Heading]]` and `[text](#Heading)` can land
  // on a section; duplicates get a numeric suffix.
  const headingIdFor = (text) => {
    const base = 'h-' + (slugifyPath(text) || 'section')
    if (headingIds[base] === undefined) { headingIds[base] = 1; return base }
    headingIds[base] += 1
    return base + '-' + headingIds[base]
  }
  let i = 0
  let para = []
  const flushPara = () => { if (para.length) { blocks.push(React.createElement('p', null, inline(para.join(' '), o))); para = [] } }
  let footnotesFlushed = false
  const flushFootnotes = () => {
    if (footnotesFlushed) return
    footnotesFlushed = true
    const fids = Object.keys(o.footnotes)
    if (!fids.length || !o.renderFootnotes) return
    const kids = [React.createElement('div', { className: 'pha-fn-h' }, 'Footnotes')]
    for (const id of fids) {
      kids.push(React.createElement('p', { className: 'pha-fn', key: id, id: 'fn-' + id },
        React.createElement('span', { className: 'pha-fn-num' }, '[' + id + '] '),
        ...inline(o.footnotes[id], o),
        React.createElement('a', {
          className: 'pha-fn-back',
          href: '#fnref-' + id,
          title: 'back to the reference',
          onClick: (ev) => { if (o.onAnchor) { ev.preventDefault(); o.onAnchor('fnref-' + id) } },
        }, '↩'),
      ))
    }
    blocks.push(React.createElement('section', { className: 'pha-footnotes' }, kids))
  }
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()
    if (trimmed === '') { flushPara(); if (Object.keys(o.footnotes).length) flushFootnotes(); i++; continue }
    const fn = trimmed.match(/^\[\^(\d+)\]\s*:\s*(.*)$/)
    if (fn && !trimmed.startsWith('|')) { o.footnotes[fn[1]] = fn[2]; i++; continue }
    const BS = String.fromCharCode(92)
    if (trimmed.charAt(0) === BS && trimmed.charAt(1) === '(' && trimmed.charAt(2) === '^') {
      const restIn = trimmed.slice(3)
      if (restIn.charAt(0) === '{') {
        const close = restIn.indexOf('}')
        if (close > 0) {
          const num = restIn.slice(1, close)
          const after = restIn.slice(close + 1)
          if (after.charAt(0) === BS && after.charAt(1) === ')') {
            o.footnotes[num] = after.slice(2).trim()
            i++
            continue
          }
        }
      }
    }
    if (trimmed.startsWith('```')) {
      flushPara(); const code = []; i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) { code.push(lines[i]); i++ }
      i++
      blocks.push(React.createElement('pre', { className: 'pha-md-code' }, code.join('\n')))
      continue
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { flushPara(); blocks.push(React.createElement('hr')); i++; continue }
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      flushPara()
      blocks.push(React.createElement('h' + Math.min(6, h[1].length), { id: headingIdFor(h[2]) }, inline(h[2], o)))
      i++
      continue
    }
    const bq = trimmed.match(/^>\s?(.*)$/)
    if (bq) { flushPara(); blocks.push(React.createElement('blockquote', null, inline(bq[1], o))); i++; continue }
    if (trimmed.startsWith('|') && lines[i + 1] && lines[i + 1].trim().match(/^\|[\s:|\-]+\|$/)) {
      flushPara(); const rows = []
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(lines[i]); i++ }
      const parseRow = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
      const header = parseRow(rows[0])
      const bodyRows = rows.slice(1).filter((r) => !/^\|[\s:|\-]+\|$/.test(r.trim())).map((r) => parseRow(r))
      blocks.push(React.createElement('table', { className: 'pha-md-table' },
        React.createElement('thead', null, React.createElement('tr', null, header.map((c, k) => React.createElement('th', { key: k }, inline(c, o))))),
        React.createElement('tbody', null, bodyRows.map((r, ri) => React.createElement('tr', { key: ri }, r.map((c, ci) => React.createElement('td', { key: ci }, inline(c, o)))))),
      ))
      continue
    }
    const ul = trimmed.match(/^([-*+])\s+(.*)$/)
    const ol = trimmed.match(/^(\d+)[.)]\s+(.*)$/)
    if (ul || ol) {
      flushPara(); const isOl = !ul; const items = []
      while (i < lines.length) {
        const ln = lines[i].trim()
        const m1 = ln.match(/^([-*+])\s+(.*)$/)
        const m2 = ln.match(/^(\d+)[.)]\s+(.*)$/)
        if (isOl && m2) { items.push(m2[2]); i++; continue }
        if (!isOl && m1) { items.push(m1[2]); i++; continue }
        break
      }
      blocks.push(React.createElement(isOl ? 'ol' : 'ul', null, items.map((it, k) => React.createElement('li', { key: k }, inline(it, o)))))
      continue
    }
    para.push(trimmed)
    i++
  }
  flushPara()
  flushFootnotes()
  return blocks
}

// Split a definition file into its YAML front matter (the interface/config keys)
// and its markdown body, so the view can show the config verbatim and render the
// prose (palaeographer / editor rules are markdown).
function splitFront(text) {
  const m = String(text || '').match(/^---\n([\s\S]*?)\n---\n?/)
  return m
    ? { front: m[1], body: String(text).slice(m[0].length) }
    : { front: null, body: String(text || '') }
}

function renderNote(content, linkOpts) {
  const o = Object.assign({ footnotes: {}, renderFootnotes: true }, linkOpts || {})
  let body = String(content || '')
  let title = null
  const fm = body.match(/^---\n([\s\S]*?)\n---\n?/)
  if (fm) {
    const t = fm[1].match(/^title:\s*(.+)$/m)
    if (t) title = t[1].replace(/^["']|["']$/g, '')
    body = body.slice(fm[0].length)
  }
  return React.createElement('div', { className: 'pha-md' },
    title ? React.createElement('h1', null, title) : null,
    renderMd(body, o),
  )
}

const CSS = [
  '.pha-root{display:flex;flex-direction:column;flex:1 1 auto;min-height:0;overflow:hidden;font-size:13px;color:var(--dsw-alias-label-primary,inherit);background:var(--dsw-alias-bg-base,transparent)}',
  '.pha-top{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--dsw-alias-border-l1,#333);flex:0 0 auto}',
  '.pha-title{font-weight:600;font-size:13px}', '.pha-muted{opacity:.6;font-size:12px}',
  '.pha-main{flex:1 1 0;display:flex;min-height:0;overflow:hidden}',
  '.pha-left{min-width:200px;max-width:72%;overflow:auto;overscroll-behavior:contain;padding:8px;flex:0 0 auto}',
  '.pha-search{display:flex;gap:6px;align-items:center;padding:0 0 6px;border-bottom:1px solid var(--dsw-alias-border-l1,#333);position:sticky;top:0;background:var(--dsw-alias-bg-base,transparent);z-index:2}',
  '.pha-split{width:6px;flex:0 0 6px;cursor:col-resize;background:var(--dsw-alias-border-l1,#333);touch-action:none}',
  '.pha-split:hover,.pha-split.drag{background:var(--dsw-alias-brand-primary,#0b5fff)}',
  '.pha-right{flex:1;min-width:0;overflow:auto;overscroll-behavior:contain;padding:12px;display:flex;flex-direction:column;gap:10px}',
  '.pha-group{margin-bottom:10px}',
  '.pha-group-h{font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:4px 2px;color:var(--dsw-alias-label-secondary,inherit)}',
  '.pha-doc{display:flex;align-items:center;gap:6px;padding:5px 6px;border-radius:6px;cursor:pointer;border:1px solid transparent}',
  '.pha-doc:hover{background:var(--dsw-alias-bg-layer-1,#f2f2f2)}',
  '.pha-doc.sel{background:var(--dsw-alias-bg-layer-2,#e8e8e8);border-color:var(--dsw-alias-border-l2,#999)}',
  '.pha-doc-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
  '.pha-doc-meta{font-size:11px;opacity:.6;white-space:nowrap}',
  '.pha-chip{font-size:10px;padding:1px 6px;border-radius:10px;border:1px solid var(--dsw-alias-border-l2,#777);white-space:nowrap}',
  '.pha-chip.ok{border-color:transparent;background:color-mix(in srgb,var(--dsw-alias-state-success-primary,#2f9e44) 18%,transparent);color:var(--dsw-alias-state-success-primary,#2f9e44)}',
  '.pha-chip.busy{border-color:transparent;background:color-mix(in srgb,var(--dsw-alias-state-warn-primary,#e8890c) 18%,transparent);color:var(--dsw-alias-state-warn-primary,#e8890c)}',
  '.pha-chip.bad{border-color:transparent;background:color-mix(in srgb,var(--dsw-alias-state-error-primary,#e03131) 18%,transparent);color:var(--dsw-alias-state-error-primary,#e03131)}',
  '.pha-chip.dim{opacity:.6}',
  '.pha-btn{font:inherit;font-size:12px;padding:3px 9px;border-radius:6px;border:1px solid var(--dsw-alias-border-l2,#666);background:var(--dsw-alias-bg-layer-1,#fff);color:var(--dsw-alias-label-primary,inherit);cursor:pointer}',
  '.pha-btn:hover{filter:brightness(1.08)}',
  '.pha-btn.primary{border-color:transparent;background:var(--dsw-alias-brand-primary,#0b5fff);color:#fff}',
  '.pha-btn.on{border-color:transparent;background:var(--dsw-alias-state-warn-primary,#e8890c);color:#fff}',
  '.pha-btn.small{padding:1px 7px;font-size:11px}',
  '.pha-btn[disabled]{opacity:.45;cursor:not-allowed}',
  '.pha-spacer{flex:1 1 auto}',
  '.pha-err{color:var(--dsw-alias-state-error-primary,#e03131);font-size:12px;line-height:1.45}',
  '.pha-input{font:inherit;font-size:12px;padding:3px 8px;border-radius:6px;border:1px solid var(--dsw-alias-border-l2,#666);background:var(--dsw-alias-bg-layer-1,#fff);color:inherit;flex:1;min-width:120px}',
  '.pha-pre{white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Menlo,monospace;font-size:12px;line-height:1.5;padding:10px;border-radius:8px;border:1px solid var(--dsw-alias-border-l1,#333);background:var(--dsw-alias-bg-layer-1,#0f1216);max-height:60vh;overflow:auto}',
  '.pha-md{font-size:13px;line-height:1.55;color:var(--dsw-alias-label-primary,inherit)}',
  '.pha-md p{margin:0 0 8px}',
  '.pha-md h1,.pha-md h2,.pha-md h3,.pha-md h4,.pha-md h5,.pha-md h6{margin:10px 0 6px;font-weight:650;line-height:1.3}',
  '.pha-md ul,.pha-md ol{margin:0 0 8px;padding-left:20px}', '.pha-md li{margin:2px 0}',
  '.pha-md code{background:var(--dsw-alias-bg-layer-2,#e8e8e8);padding:1px 4px;border-radius:4px;font-family:ui-monospace,Menlo,monospace;font-size:12px}',
  '.pha-md pre.pha-md-code{background:var(--dsw-alias-bg-layer-1,#0f1216);border:1px solid var(--dsw-alias-border-l1,#333);padding:8px 10px;border-radius:6px;overflow:auto;font-family:ui-monospace,Menlo,monospace;font-size:12px;white-space:pre-wrap;margin:0 0 8px}',
  '.pha-md blockquote{margin:0 0 8px;padding:2px 10px;border-left:3px solid var(--dsw-alias-border-l2,#666);color:var(--dsw-alias-label-secondary,inherit)}',
  '.pha-md a{color:var(--dsw-alias-brand-primary,#0b5fff);text-decoration:underline}',
  '.pha-md hr{border:none;border-top:1px solid var(--dsw-alias-border-l2,#666);margin:10px 0}',
  '.pha-md-table{border-collapse:collapse;margin:0 0 8px;font-size:12px}',
  '.pha-md-table th,.pha-md-table td{border:1px solid var(--dsw-alias-border-l1,#333);padding:3px 6px;text-align:left}',
  '.pha-md-table th{background:var(--dsw-alias-bg-layer-2,#e8e8e8)}',
  '.pha-footnotes{margin:14px 0 0;padding-top:8px;border-top:1px solid var(--dsw-alias-border-l1,#333);font-size:12px}',
  '.pha-fn-h{font-weight:600;margin:0 0 6px}',
  '.pha-fn{display:block;margin:0 0 6px;line-height:1.45;padding-left:1.8em;text-indent:-1.8em}',
  '.pha-fn-num{color:var(--dsw-alias-label-secondary,inherit)}',
  '.pha-note-link{color:var(--dsw-alias-brand-primary,#0b5fff);cursor:pointer;text-decoration:underline}',
  '.pha-note-link.dangling{color:var(--dsw-alias-state-warn-primary,#e8890c);text-decoration-style:dotted}',
  '.pha-page-link{color:var(--dsw-alias-brand-primary,#0b5fff);cursor:pointer;text-decoration:underline}',
  '.pha-ext-link{color:var(--dsw-alias-brand-primary,#0b5fff);text-decoration:underline}',
  '.pha-link-tag{margin-left:4px;font-size:10px;opacity:.65}',
  '.pha-ext-mark{margin-left:2px;font-size:10px;opacity:.6}',
  '.pha-fn-back{margin-left:6px;text-decoration:none;opacity:.6}',
  '.pha-content{display:flex;gap:12px;align-items:flex-start}',
  '.pha-media{flex:0 0 45%;max-width:45%;position:sticky;top:0}',
  '.pha-text{flex:1;min-width:0}',
  '.pha-img{max-width:100%;max-height:72vh;object-fit:contain;border:1px solid var(--dsw-alias-border-l1,#333);border-radius:6px;background:var(--dsw-alias-bg-layer-1,transparent)}',
  '.pha-pages{display:flex;flex-direction:column;gap:6px}',
  '.pha-jump{display:flex;gap:6px;align-items:center;flex-wrap:wrap}',
  '.pha-num{font:inherit;font-size:12px;padding:2px 6px;width:64px;border-radius:5px;border:1px solid var(--dsw-alias-border-l2,#666);background:var(--dsw-alias-bg-layer-1,#fff);color:inherit}',
  '.pha-range{flex:1;min-width:110px;max-width:260px}',
  '.pha-strip{display:flex;flex-wrap:wrap;gap:4px;max-height:150px;overflow:auto;padding:6px;border:1px solid var(--dsw-alias-border-l1,#333);border-radius:6px}',
  '.pha-page{min-width:30px;text-align:center;padding:2px 4px;border-radius:5px;border:1px solid var(--dsw-alias-border-l2,#666);cursor:pointer;font-size:11px}',
  '.pha-page:hover{background:var(--dsw-alias-bg-layer-2,#e8e8e8)}',
  '.pha-page.sel{background:var(--dsw-alias-brand-primary,#0b5fff);color:#fff;border-color:transparent}',
  '.pha-page.pending{border-color:var(--dsw-alias-state-warn-primary,#e8890c);color:var(--dsw-alias-state-warn-primary,#e8890c);font-weight:600}',
  '.pha-page.pending.sel{background:var(--dsw-alias-state-warn-primary,#e8890c);color:#fff;border-color:transparent}',
  '.pha-empty{padding:20px;text-align:center;opacity:.5}',
].join('')

function statusClass(s) {
  s = String(s || '')
  if (s === 'done' || s === 'ok') return 'ok'
  if (s === 'error') return 'bad'
  if (s === 'processing' || s === 'running' || s === 'starting' || s === 'pending') return 'busy'
  return 'dim'
}
function relPath(doc) {
  return doc ? (doc.dir_path ? doc.dir_path + '/' + doc.filename : doc.filename) : ''
}

function PhaView() {
  const h = React.createElement
  const [state, setState] = React.useState({ docs: null, docsErr: null, archive: null, selectedId: null, detail: null, hits: null, searchMode: false, notes: null, selectedNote: null, noteMode: false, page: null, pageReq: null, pageErr: null, editMsg: null, pendingPages: null, pendingNeeds: null, defs: null, selectedDef: null, defMode: false, defMsg: null, config: null, configMode: false, configMsg: null, collEncoders: null, plainOverride: null, pageErr: null, pageLoading: false, noteMsg: null })
  const [searchText, setSearchText] = React.useState('')
  const [jump, setJump] = React.useState('')
  const [range, setRange] = React.useState(1)
  const [leftPct, setLeftPct] = React.useState(38)
  const [dragging, setDragging] = React.useState(false)
  const [showImg, setShowImg] = React.useState(false)
  const [textOn, setTextOn] = React.useState(true)
  const [imgData, setImgData] = React.useState(null)
  const [rootEl, setRootEl] = React.useState(null)
  const [noteAnchor, setNoteAnchor] = React.useState(null)

  React.useEffect(() => {
    // The document list first — it is what the user waits for. Notes and the
    // definition folders follow once it has landed: all of these share a single
    // archive discovery in the host, so loading them in parallel only makes the
    // list wait on work it does not need.
    get('/pha/documents')
      .then((r) => setState((s) => ({ ...s, docs: r && r.ok ? r.documents : null, docsErr: r && r.ok ? null : ((r && r.error) || 'documents call failed'), archive: (r && r.archive) || s.archive })))
      .catch((e) => setState((s) => ({ ...s, docsErr: String((e && e.message) || e) })))
      .then(() => {
        get('/pha/notes').then((r) => setState((s) => ({ ...s, notes: r && r.ok ? r.notes : [] }))).catch(() => {})
        get('/pha/defs').then((r) => setState((s) => ({ ...s, defs: r && r.ok ? r.defs : [] }))).catch(() => {})
        get('/pha/collectionEncoders').then((r) => setState((s) => ({ ...s, collEncoders: r && r.ok ? r.encoders : [] }))).catch(() => {})
      })
  }, [])

  const pageNo = state.pageReq ? state.pageReq.page : null
  React.useEffect(() => {
    if (!state.selectedNote || !noteAnchor) return
    scrollToAnchor(noteAnchor)
  }, [state.selectedNote, noteAnchor])

  React.useEffect(() => {
    if (!showImg || !state.pageReq) { setImgData(null); return }
    let alive = true
    get('/pha/pageImage?doc=' + encodeURIComponent(state.pageReq.doc) + '&page=' + state.pageReq.page).then((r) => { if (alive) setImgData(r && r.ok ? r.dataUrl : null) }).catch(() => { if (alive) setImgData(null) })
    return () => { alive = false }
  }, [showImg, pageNo])

  // Pin the root to the visible area so left and right panes scroll independently.
  React.useEffect(() => {
    function fit() {
      const el = rootEl
      if (!el || !el.ownerDocument || !el.ownerDocument.defaultView) return
      const doc = el.ownerDocument
      const win = doc.defaultView
      let scroller = null
      let node = el.parentElement
      while (node && node !== doc.documentElement) {
        try {
          const cs = win.getComputedStyle(node)
          const oy = cs.overflowY
          if ((oy === 'auto' || oy === 'scroll') && node.clientHeight > 0 && node.scrollHeight > node.clientHeight + 1) { scroller = node; break }
        } catch (e) {}
        node = node.parentElement
      }
      let h = null
      try {
        const er = el.getBoundingClientRect()
        if (scroller) { const sr = scroller.getBoundingClientRect(); h = sr.top + sr.height - er.top } else { h = win.innerHeight - er.top }
      } catch (e) { return }
      if (h && h > 80) { const px = Math.floor(h) + 'px'; if (el.style.height !== px) el.style.height = px }
    }
    fit()
    const iv = setInterval(fit, 900)
    return () => clearInterval(iv)
  }, [rootEl])

  // Which library page files of this document were edited but not imported yet
  // (DB out of sync with disk). Kept separate from the detail fetch so the page
  // renders first — the check can take a moment on a large document.
  async function loadPending(docId) {
    setState((s) => ({ ...s, pendingPages: null, pendingNeeds: null }))
    try {
      const r = await get('/pha/pending?doc=' + encodeURIComponent(String(docId)))
      setState((s) => ({ ...s, pendingPages: (r && r.ok) ? (r.pending || []) : null, pendingNeeds: (r && r.ok) ? r.needs : null }))
    } catch (e) { setState((s) => ({ ...s, pendingPages: null, pendingNeeds: null })) }
  }
  async function openDoc(id) {
    setState((s) => ({ ...s, selectedId: id, noteMode: false, selectedNote: null, page: null, pageReq: null, pageErr: null, pendingPages: null, pendingNeeds: null, configMode: false, config: null, configMsg: null }))
    const r = await get('/pha/document?doc=' + encodeURIComponent(id))
    setState((s) => ({ ...s, detail: r && r.ok ? { doc: r.doc, pages: r.pages || [], edits: r.edits || [], matched: false } : null }))
    loadPending(id)
  }
  async function openSearchDoc(id) {
    const r = await get('/pha/document?doc=' + encodeURIComponent(id))
    const matched = (state.hits || []).filter((hit) => hit.document_id === id)
    const vmap = {}
    matched.forEach((hit) => { vmap[hit.page_no] = hit.variant })
    const pages = r && r.ok && r.pages ? r.pages.filter((p) => vmap[p.page_no] !== undefined) : []
    setState((s) => ({ ...s, selectedId: id, detail: r && r.ok ? { doc: r.doc, pages, edits: r.edits || [], matched: true } : null, searchPageVariant: vmap, noteMode: false, selectedNote: null, page: null, pageReq: null, pageErr: null, pendingPages: null, pendingNeeds: null, configMode: false, config: null, configMsg: null }))
    loadPending(id)
  }
  async function openDef(d) {
    setState((s) => ({ ...s, defMode: true, selectedDef: null, defMsg: null, noteMode: false, selectedNote: null, selectedId: null, detail: null, page: null, pageReq: null, pageErr: null, pendingPages: null, pendingNeeds: null, configMode: false, config: null, configMsg: null }))
    try {
      const r = await get('/pha/def?path=' + encodeURIComponent(d.path))
      setState((s) => ({ ...s, selectedDef: (r && r.ok) ? { kind: r.kind, name: r.name, path: r.path, content: r.content } : null, defMsg: (r && !r.ok) ? ((r && r.error) || 'load failed') : null }))
    } catch (e) { setState((s) => ({ ...s, defMsg: String((e && e.message) || e) })) }
  }
  async function defEdit() {
    const d = state.selectedDef
    if (!d) return
    try {
      const r = await get('/pha/open?path=' + encodeURIComponent(d.path))
      setState((s) => ({ ...s, defMsg: (r && r.ok) ? ('opened: ' + (r.path || d.path) + ' — save in your editor, then Refresh') : ((r && r.error) || 'open failed') }))
    } catch (e) { setState((s) => ({ ...s, defMsg: String((e && e.message) || e) })) }
  }
  async function openConfig() {
    const sel = state.selectedId
    if (!sel) return
    setState((s) => ({ ...s, configMode: true, config: null, configMsg: null }))
    try {
      const r = await get('/pha/config?doc=' + encodeURIComponent(String(sel)))
      setState((s) => ({ ...s, config: (r && r.ok) ? r : null, configMsg: (r && !r.ok) ? ((r && r.error) || 'config failed') : null }))
    } catch (e) { setState((s) => ({ ...s, configMsg: String((e && e.message) || e) })) }
  }
  async function configEdit() {
    const c = state.config
    if (!c || !c.path) return
    try {
      const r = await get('/pha/open?path=' + encodeURIComponent(c.path))
      setState((s) => ({ ...s, configMsg: (r && r.ok) ? ('opened: ' + (r.path || c.path) + ' — save in your editor, then Refresh') : ((r && r.error) || 'open failed') }))
    } catch (e) { setState((s) => ({ ...s, configMsg: String((e && e.message) || e) })) }
  }
  async function openFilePath(p) {
    if (!p) return
    let msg
    try {
      const r = await get('/pha/open?path=' + encodeURIComponent(p))
      msg = (r && r.ok) ? ('opened: ' + (r.path || p)) : ((r && r.error) || 'open failed')
    } catch (e) { msg = String((e && e.message) || e) }
    setState((s) => ({ ...s, configMsg: msg, defMsg: msg }))
  }
  async function openNote(name, anchor) {
    setState((s) => ({ ...s, noteMode: true, selectedNote: null, selectedId: null, detail: null, page: null, pageReq: null, pageErr: null, noteMsg: null, configMode: false, config: null, configMsg: null }))
    setNoteAnchor(anchor || null)
    try {
      const r = await get('/pha/note?name=' + encodeURIComponent(name))
      setState((s) => ({
        ...s,
        selectedNote: (r && r.ok) ? { name: r.name, content: r.content, path: r.path } : null,
        noteMsg: (r && !r.ok) ? ((r && r.error) || ('no note named ' + name)) : null,
      }))
    } catch (e) {
      setState((s) => ({ ...s, selectedNote: null, noteMsg: String((e && e.message) || e) }))
    }
  }

  // Scroll to an anchor inside the rendered markdown. Heading ids are `h-<slug>`, so a
  // wikilink's `#Heading` text and a markdown `#id` both find their target.
  function scrollToAnchor(raw) {
    const id = String(raw || '')
    if (!id || typeof document === 'undefined') return false
    const tries = [id, 'h-' + slugifyPath(id), slugifyPath(id)]
    for (const t of tries) {
      if (!t) continue
      const el = document.getElementById(t)
      if (el && el.scrollIntoView) { el.scrollIntoView({ block: 'start' }); return true }
    }
    return false
  }

  function docIdForSlug(slug) {
    for (const d of (state.docs || [])) if (docSlugFor(d) === slug) return d.id
    return null
  }

  // A citation link (`/doc/<slug>/p<NNN>`) points at the pha serve viewer. Resolve the
  // slug against the archive we are already browsing and open that page HERE, so a
  // footnote is readable inside the harness and does not require `pha serve`.
  async function openCitation(c) {
    if (!c || !c.slug) return
    let id = docIdForSlug(c.slug)
    if (id === null && !state.docs) {
      const r = await get('/pha/documents')
      if (r && r.ok) setState((s) => ({ ...s, docs: r.documents, archive: r.archive || s.archive }))
      for (const d of ((r && r.ok && r.documents) || [])) if (docSlugFor(d) === c.slug) id = d.id
    }
    if (id === null) {
      setState((s) => ({ ...s, noteMsg: 'no document in this archive matches /doc/' + c.slug + ' — the note may cite another archive' }))
      return
    }
    setState((s) => ({ ...s, noteMsg: null }))
    await openDoc(id)
    if (c.page) openPage(c.page, false, id)
  }
  async function openPage(pageNo, edited, docId) {
    const doc = docId === undefined || docId === null ? state.selectedId : docId
    // Re-arm the per-variant default: the raw transcription shows as plain text, the
    // edited variant as markdown. An explicit txt/md choice applies to the page on
    // screen only and is dropped when another page is opened.
    setState((s) => ({ ...s, pageReq: { doc, page: pageNo, edited: !!edited }, plainOverride: null, pageLoading: true, pageErr: null }))
    try {
      const r = await get('/pha/page?doc=' + encodeURIComponent(doc) + '&page=' + pageNo + (edited ? '&edited=1' : ''))
      // A failed page request used to blank the pane silently (e.g. --edited with no
      // edited text yet). Keep the message: it names the pass that produces the text.
      setState((s) => ({ ...s, page: r && r.ok ? r.page : null, pageLoading: false, pageErr: (r && !r.ok) ? ((r && r.error) || 'page load failed') : null }))
    } catch (e) {
      setState((s) => ({ ...s, page: null, pageLoading: false, pageErr: String((e && e.message) || e) }))
    }
  }
  async function doSearch() {
    const q = searchText.trim()
    if (!q) return
    setState((s) => ({ ...s, searchMode: true, hits: null, selectedId: null, detail: null, page: null, pageReq: null, pageErr: null, noteMode: false, selectedNote: null }))
    const r = await get('/pha/search?q=' + encodeURIComponent(q) + '&limit=20')
    setState((s) => ({ ...s, hits: r && r.ok ? r.results : null }))
  }
  function clearSearch() { setSearchText(''); setState((s) => ({ ...s, searchMode: false, hits: null, selectedId: null, detail: null, page: null, pageReq: null })) }
  async function editOpen() {
    const doc = state.detail && state.detail.doc
    const pr = state.pageReq
    if (!doc || !pr) { setState((x) => ({ ...x, editMsg: 'select a page first' })); return }
    const url = '/pha/open?doc=' + encodeURIComponent(String(doc.id)) + '&page=' + encodeURIComponent(String(pr.page)) + (pr.edited ? '&edited=1' : '')
    try {
      const r = await get(url)
      if (r && r.ok) {
        setState((x) => ({ ...x, editMsg: (r.path ? 'opened: ' + r.path : (r.output || 'opened')) + ' — edit the file, then run pha review' }))
      } else {
        setState((x) => ({ ...x, editMsg: (r && r.error) || 'open failed' }))
      }
    } catch (e) { setState((x) => ({ ...x, editMsg: String((e && e.message) || e) })) }
  }

  const s = state
  const searchMode = !!s.searchMode
  const noteNames = (s.notes || []).map((n) => n.name)
  const editors = []
  let groups = []
  if (searchMode && s.hits) {
    const byColl = {}
    for (const hit of s.hits) {
      const key = hit.collection || '(search)'
      ;(byColl[key] = byColl[key] || {})[hit.document_id] = { id: hit.document_id, filename: hit.filename, count: ((byColl[key][hit.document_id] && byColl[key][hit.document_id].count) || 0) + 1 }
    }
    const byName = (a, b) => String(a.filename || '').localeCompare(String(b.filename || ''), undefined, { sensitivity: 'base' })
    for (const key of Object.keys(byColl).sort()) groups.push({ key, docs: Object.values(byColl[key]).sort(byName) })
  } else if (s.docs) {
    const map = {}
    for (const d of s.docs) { const key = d.dir_path || '(root)'; (map[key] = map[key] || []).push(d) }
    const byName = (a, b) => String(a.filename || '').localeCompare(String(b.filename || ''), undefined, { sensitivity: 'base' })
    for (const key of Object.keys(map).sort()) groups.push({ key, docs: map[key].sort(byName) })
  }
  if (s.detail && s.detail.edits) { const seen = {}; for (const e of s.detail.edits) if (!seen[e.editor]) { seen[e.editor] = true; editors.push(e.editor) } }

  function startDrag(e) { e.currentTarget.setPointerCapture(e.pointerId); setDragging(true) }
  function dragMove(e) {
    // Only resize while a mouse/pen button is actually held. Hovering the splitter
    // fires pointermove with buttons===0; without this guard a stuck `dragging`
    // (e.g. a pointer-capture that never saw its up event) would keep growing the
    // left pane just from moving the cursor over it.
    if (!dragging || e.buttons === 0) return
    const par = e.currentTarget.parentElement
    const rect = par ? par.getBoundingClientRect() : null
    if (!rect || rect.width === 0) return
    setLeftPct(Math.min(66, Math.max(16, Math.round(((e.clientX - rect.left) / rect.width) * 100))))
  }
  function endDrag() { setDragging(false) }
  function goTo(v) {
    const total = s.detail ? Math.max(1, (s.detail.pages || []).length) : 0
    const page = Number(v)
    if (!Number.isFinite(page) || page < 1 || page > total) { setJump(String(v)); return }
    openPage(page, !!(s.pageReq && s.pageReq.edited)); setRange(page); setJump('')
  }

  const top = h('div', { className: 'pha-top' },
    h('span', { className: 'pha-title' }, 'pha archive'),
    h('span', { className: 'pha-muted' }, s.archive || '…'),
    h('span', { className: 'pha-spacer' }),
    h('button', { className: 'pha-btn', onClick: () => { if (s.searchMode) clearSearch(); get('/pha/documents').then((r) => setState((x) => ({ ...x, docs: r && r.ok ? r.documents : null }))); get('/pha/notes').then((r) => setState((x) => ({ ...x, notes: r && r.ok ? r.notes : [] }))); get('/pha/defs').then((r) => setState((x) => ({ ...x, defs: r && r.ok ? r.defs : [] }))); get('/pha/collectionEncoders').then((r) => setState((x) => ({ ...x, collEncoders: r && r.ok ? r.encoders : [] }))) } }, '⟳ Refresh'),
  )

  const searchHeader = h('div', { className: 'pha-search' },
    h('input', { className: 'pha-input', placeholder: 'search the archive…', value: searchText, onChange: (e) => setSearchText(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') doSearch() } }),
    h('button', { className: 'pha-btn primary', onClick: doSearch }, 'Search'),
    searchMode ? h('button', { className: 'pha-btn small', title: 'clear search', onClick: clearSearch }, '✕') : null,
  )

  const noteList = (s.notes && s.notes.length) ? h('div', { className: 'pha-group' },
    h('div', { className: 'pha-group-h' }, 'notes  (' + s.notes.length + ')'),
    s.notes.map((n) => h('div', { className: 'pha-doc' + (s.selectedNote && s.selectedNote.name === n.name ? ' sel' : ''), key: n.name, onClick: () => openNote(n.name) },
      h('span', { className: 'pha-chip dim' }, 'note'),
      h('span', { className: 'pha-doc-name', title: n.file }, n.name),
    )),
  ) : null

  // Archive definition folders (models / palaeographers / editors) — pick one to
  // read it; Edit opens it in the OS-default editor via `pha open <path>`.
  const DEF_LABEL = { palaeographers: 'palaeographer', editors: 'editor', encoders: 'encoder', models: 'model' }
  const defGroupFor = (kind) => {
    const items = (s.defs || []).filter((d) => d.kind === kind)
    if (!items.length) return null
    return h('div', { className: 'pha-group', key: 'def-' + kind },
      h('div', { className: 'pha-group-h' }, kind + '  (' + items.length + ')'),
      items.map((d) => h('div', { className: 'pha-doc' + (s.selectedDef && s.selectedDef.path === d.path ? ' sel' : ''), key: d.path, onClick: () => openDef(d) },
        h('span', { className: 'pha-chip dim' }, DEF_LABEL[kind]),
        h('span', { className: 'pha-doc-name', title: d.path }, d.name),
      )),
    )
  }
  // Reading/extraction rules first — palaeographer, editor, encoders — and the
  // per-collection encoders render right after the top-level encoders/ section so
  // the encoders read as one block. The model interfaces they pair with come last.
  const defGroups = ['palaeographers', 'editors', 'encoders'].map(defGroupFor).filter(Boolean)
  const modelGroups = ['models'].map(defGroupFor).filter(Boolean)

  // Collection-local encoders travel with the documents; group them under the
  // collection that owns them so they sit next to their documents.
  const collEncGroups = (() => {
    const by = {}
    for (const e of (s.collEncoders || [])) (by[e.collection] = by[e.collection] || []).push(e)
    return Object.keys(by).sort().map((coll) => h('div', { className: 'pha-group', key: 'collenc-' + coll },
      h('div', { className: 'pha-group-h' }, 'encoders · ' + coll.replace(/^collections\//, '') + '  (' + by[coll].length + ')'),
      by[coll].slice().sort((a, b) => String(a.name).localeCompare(String(b.name))).map((e) => h('div', { className: 'pha-doc' + (s.selectedDef && s.selectedDef.path === e.path ? ' sel' : ''), key: e.path, onClick: () => openDef(e) },
        h('span', { className: 'pha-chip dim' }, 'encoder'),
        h('span', { className: 'pha-doc-name', title: e.path }, e.name),
      )),
    ))
  })()

  let body
  if (searchMode) {
    body = (s.hits && s.hits.length) ? groups.map((g) => h('div', { className: 'pha-group', key: g.key },
      h('div', { className: 'pha-group-h' }, g.key + '  (' + g.docs.length + ')'),
      g.docs.map((d) => h('div', { className: 'pha-doc' + (s.selectedId === d.id ? ' sel' : ''), key: d.id, onClick: () => openSearchDoc(d.id) },
        h('span', { className: 'pha-chip busy' }, d.count + ' hit' + (d.count > 1 ? 's' : '')),
        h('span', { className: 'pha-doc-name', title: d.filename }, d.filename),
      )),
    )) : h('div', { className: 'pha-empty' }, 'No matches')
  } else {
    body = s.docsErr ? h('div', { className: 'pha-err' }, s.docsErr) : (s.docs ? h('div', null,
      groups.map((g) => h('div', { className: 'pha-group', key: g.key },
        h('div', { className: 'pha-group-h' }, g.key + '  (' + g.docs.length + ')'),
        g.docs.map((d) => h('div', { className: 'pha-doc' + (s.selectedId === d.id ? ' sel' : ''), key: d.id, onClick: () => openDoc(d.id) },
          h('span', { className: 'pha-chip ' + statusClass(d.status) }, d.status || '?'),
          h('span', { className: 'pha-doc-name', title: d.filename }, d.filename),
          h('span', { className: 'pha-doc-meta' }, (d.page_count || 0) + 'p' + (d.palaeographer ? ' · ' + d.palaeographer : '')),
        )),
      )),
      noteList,
      defGroups,
      collEncGroups,
      modelGroups,
    ) : h('div', { className: 'pha-empty' }, 'Loading documents…'))
  }
  const left = h('div', { className: 'pha-left', style: { width: leftPct + '%' } }, searchHeader, body)

  let right
  if (s.configMode) {
    const c = s.config
    const res = c && c.resolved
    right = h('div', { className: 'pha-right' },
      h('div', null,
        h('strong', null, 'collection config' + (c && c.target ? ' — ' + c.target : '')),
        // the path of the pha.yaml actually in scope, above its content
        h('div', { className: 'pha-muted' }, (c && c.path) || '…'),
      ),
      (c && c.inherited) ? h('div', { className: 'pha-muted' },
        'inherited from an upper folder — this directory has no pha.yaml of its own (none is created)') : null,
      (c && c.generated) ? h('div', { className: 'pha-muted', style: { color: 'var(--dsw-alias-state-warn-primary,#e8890c)' } },
        'generated pha.yaml from the legacy configuration — review it') : null,
      (c && c.legacy_files && c.legacy_files.length) ? h('div', { className: 'pha-muted' },
        'legacy selection file(s) still present: ' + c.legacy_files.join(', ')) : null,
      h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' } },
        h('button', { className: 'pha-btn small', disabled: !(c && c.path), title: 'Open this pha.yaml in your default editor', onClick: configEdit }, 'Edit'),
        h('button', { className: 'pha-btn small', onClick: () => setState((x) => ({ ...x, configMode: false })) }, '← back to page'),
      ),
      s.configMsg ? h('div', { className: 'pha-muted', style: { fontSize: 12 } }, s.configMsg) : null,
      res ? h('div', null,
        h('div', { className: 'pha-muted' }, 'palaeographer: ' + (res.palaeographer.id || '—')
          + (res.palaeographer.model ? ' · ' + res.palaeographer.model : '')
          + (res.palaeographer.source ? '  ← ' + res.palaeographer.source : '')),
        h('div', { className: 'pha-muted' }, 'editor: ' + (res.editor.id || 'none')
          + (res.editor.model ? ' · ' + res.editor.model : '')
          + (res.editor.source ? '  ← ' + res.editor.source : '')),
        (res.problems && res.problems.length) ? h('div', { className: 'pha-err' }, 'problems: ' + res.problems.join('; ')) : null,
        (res.encoders && res.encoders.length)
          ? h('div', { className: 'pha-muted' }, 'encoders (' + res.encoders.length + '):')
          : h('div', { className: 'pha-muted' }, 'encoders: none'),
        (res.encoders || []).map((e) => h('div', { className: 'pha-doc', key: 'enc-' + e.id + (e.path || ''), style: { cursor: 'default' } },
          h('span', { className: 'pha-chip dim' }, 'encoder'),
          h('span', { className: 'pha-doc-name', title: e.path || '' },
            e.id + (e.model ? ' · ' + e.model : '') + (e.pages ? ' · pages ' + e.pages : '')),
          e.path ? h('button', { className: 'pha-btn small', title: 'Open this encoder in your default editor', onClick: () => openFilePath(e.path) }, 'Edit') : null,
        )),
      ) : null,
      (c && c.content) ? h('pre', { className: 'pha-pre' }, c.content)
        : h('div', { className: 'pha-empty' }, s.configMsg || 'Loading configuration…'),
    )
  } else if (s.defMode && s.selectedDef) {
    const fd = splitFront(s.selectedDef.content)
    right = h('div', { className: 'pha-right' },
      h('div', null,
        h('strong', null, s.selectedDef.kind + ' / ' + s.selectedDef.name),
        h('div', { className: 'pha-muted' }, s.selectedDef.path),
      ),
      h('div', { style: { display: 'flex', gap: 6, alignItems: 'center' } },
        h('button', { className: 'pha-btn small', title: 'Open this file in your default editor (the OS picks the app)', onClick: defEdit }, 'Edit'),
      ),
      s.defMsg ? h('div', { className: 'pha-muted', style: { fontSize: 12 } }, s.defMsg) : null,
      fd.front ? h('pre', { className: 'pha-pre', style: { maxHeight: '30vh' } }, fd.front) : null,
      h('div', { className: 'pha-md' }, renderMd(fd.body)),
    )
  } else if (s.defMode && !s.selectedDef) {
    right = h('div', { className: 'pha-empty' }, s.defMsg || 'Loading definition…')
  } else if (s.noteMode && s.selectedNote) {
    right = h('div', { className: 'pha-right' },
      h('div', null, h('strong', null, s.selectedNote.name), h('div', { className: 'pha-muted' }, s.selectedNote.path)),
      s.noteMsg ? h('div', { className: 'pha-err' }, s.noteMsg) : null,
      renderNote(s.selectedNote.content, {
        onNote: openNote,
        onCitation: openCitation,
        onAnchor: scrollToAnchor,
        noteNames: noteNames,
      }),
    )
  } else if (s.noteMode && !s.selectedNote) {
    right = h('div', { className: 'pha-empty' }, s.noteMsg || 'Loading note…')
  } else if (!s.detail) right = h('div', { className: 'pha-empty' }, searchMode ? 'Select a matching document to see its matched pages.' : 'Select a document or note to read it.')
  else if (!s.detail.doc) right = h('div', { className: 'pha-empty' }, 'Document not found in the archive.')
  else {
    const d = s.detail.doc
    const totalPages = d.page_count || Math.max(1, (s.detail.pages || []).length)
    const curPage = s.pageReq ? s.pageReq.page : null
    const selIsEdited = !!(s.pageReq && s.pageReq.edited)
    const effRange = (curPage && curPage >= 1 && curPage <= totalPages) ? curPage : range
    const pv = s.page
    const listPages = s.detail.pages || []
    // Raw transcription reads best as plain text (a faithful transcript whose incidental
    // markup should stay visible); the edited variant as rendered markdown.
    const plain = s.plainOverride === null ? !selIsEdited : s.plainOverride
    const hasEdited = editors.length > 0
    const pendingSet = new Set((s.pendingPages || []).map((x) => x.page_no))
    const pendingCount = (s.pendingPages || []).length
    // image-only mode: let the render fill the pane instead of sharing half with text
    const mediaBlock = showImg
      ? h('div', { className: 'pha-media', style: textOn ? null : { flex: '0 0 auto', maxWidth: '94%' } },
          imgData ? h('img', { className: 'pha-img', src: imgData, alt: 'page' }) : h('div', { className: 'pha-empty' }, 'No render image'))
      : null
    let textBlock = null
    if (textOn) {
      textBlock = s.pageErr ? h('div', { className: 'pha-text' },
        h('div', { className: 'pha-err' }, s.pageErr),
        (s.pageReq && s.pageReq.edited && !hasEdited) ? h('div', { className: 'pha-muted' },
          'this document has no edited text in the archive yet — run that pass, then Refresh.') : null,
      ) : pv ? h('div', { className: 'pha-text' },
        h('div', { className: 'pha-muted' }, 'page ' + (s.pageReq ? s.pageReq.page : '') + ' · ' + (pv.variant || '') + (pv.reviewed ? ' · reviewed' : '')),
        h('div', { className: 'pha-muted' }, pv.page_file || ''),
        plain ? h('pre', { className: 'pha-pre' }, pv.text || '(empty)') : h('div', { className: 'pha-md' }, renderMd(pv.text || '')),
      ) : (s.pageLoading ? h('div', { className: 'pha-empty' }, 'Loading page…') : h('div', { className: 'pha-empty' }, 'Select a page to read its text.'))
    }
    right = h('div', { className: 'pha-right' },
      h('div', null, h('strong', null, '#' + d.id + ' ' + d.filename), h('div', { className: 'pha-muted' }, (d.path || '') + ' · ' + (d.kind || ''))),
      h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' } },
        h('span', { className: 'pha-chip ' + statusClass(d.status) }, d.status || '?'),
        h('span', { className: 'pha-chip' }, (d.page_count || 0) + ' pages'),
        h('button', { className: 'pha-btn small', title: "Show this collection's pha.yaml configuration (resolved, generating it only when none is in scope)", onClick: openConfig }, 'Config'),
      ),
      h('div', { className: 'pha-pages' },
        h('div', { className: 'pha-jump' },
          h('span', { className: 'pha-muted' }, (searchMode ? listPages.length + ' matched / ' : '') + totalPages + ' pages'),
          h('input', { className: 'pha-num', type: 'number', min: 1, max: totalPages, value: jump, placeholder: 'page #', onChange: (e) => setJump(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') goTo(Number(jump)) } }),
          h('button', { className: 'pha-btn small', onClick: () => goTo(Number(jump)) }, 'Go'),
          h('input', { className: 'pha-range', type: 'range', min: 1, max: totalPages, value: effRange, onChange: (e) => { const v = Number(e.target.value); setRange(v); openPage(v, selIsEdited) } }),
          h('span', { className: 'pha-muted' }, effRange + ' / ' + totalPages),
        ),
        listPages.length ? h('div', { className: 'pha-strip' }, listPages.map((p) => {
          const dv = (searchMode && s.searchPageVariant) ? (s.searchPageVariant[p.page_no] === 'edited') : selIsEdited
          const pend = pendingSet.has(p.page_no)
          return h('span', { className: 'pha-page' + (curPage === p.page_no ? ' sel' : '') + (pend ? ' pending' : ''), key: p.id, title: (pend ? 'edited in the library — not imported yet (pha review)\n' : '') + 'status: ' + (p.status || '?'), onClick: () => { openPage(p.page_no, dv); setRange(p.page_no) } }, p.page_no)
        })) : h('div', { className: 'pha-muted' }, searchMode ? 'No matched pages…' : 'No pages…'),
      ),
      h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' } },
        h('button', { className: 'pha-btn small' + (showImg ? ' on' : ''), title: 'show/hide the page image', onClick: () => setShowImg(!showImg) }, 'Image'),
        h('button', { className: 'pha-btn small' + (textOn ? ' on' : ''), title: 'show/hide the text pane', onClick: () => setTextOn(!textOn) }, 'Text'),
        textOn ? h('button', { className: 'pha-btn small' + (selIsEdited ? '' : ' primary'), title: 'the raw transcription of this page', onClick: () => { if (s.pageReq) openPage(s.pageReq.page, false) } }, 'Raw') : null,
        textOn ? h('button', { className: 'pha-btn small' + (selIsEdited ? ' primary' : ''), disabled: !hasEdited, title: hasEdited ? ('the edited text of this page (editor: ' + editors.join(', ') + ')') : 'this document has no edited text yet — run: pha edit', onClick: () => { if (s.pageReq) openPage(s.pageReq.page, true) } }, hasEdited ? ('Edited (' + editors.join(', ') + ')') : 'Edited') : null,
        textOn ? h('button', { className: 'pha-btn small' + (plain ? ' on' : ''), title: plain ? 'shown as plain text — click to render it as markdown' : 'shown as rendered markdown — click for plain text', onClick: () => setState((x) => ({ ...x, plainOverride: !plain })) }, plain ? 'MD' : 'TXT') : null,
        h('span', { className: 'pha-spacer' }),
        h('button', { className: 'pha-btn small', title: 'Open this page in your default markdown editor (the OS picks the app)', onClick: editOpen }, 'Edit'),
      ),
      (!hasEdited && textOn) ? h('div', { className: 'pha-muted', style: { fontSize: 12 } },
        'no edited text in the archive for this document yet'
        + (d.editor ? ' (editor ' + d.editor + ')' : ' (no editor configured)')
        + ' — the Edited button has nothing to show until the edit pass has run.') : null,
      pendingCount ? h('div', { className: 'pha-muted', style: { fontSize: 12, color: 'var(--dsw-alias-state-warn-primary,#e8890c)' } },
        '✏️ ' + pendingCount + ' page(s) edited in the library — not imported into the archive yet. '
        + 'Run: pha review' + ((s.pendingNeeds && s.pendingNeeds.edit) ? ' · pha edit · pha reindex' : ' · pha reindex')
        + ' (page numbers in amber)') : null,
      s.editMsg ? h('div', { className: 'pha-muted', style: { fontSize: 12 } }, s.editMsg) : null,
      h('div', { className: 'pha-content' },
        mediaBlock,
        textBlock,
        (!mediaBlock && !textBlock) ? h('div', { className: 'pha-empty' }, 'Nothing to show — toggle image or text.') : null,
      ),
    )
  }

  return h('div', { className: 'pha-root', ref: setRootEl },
    top,
    h('div', { className: 'pha-main' },
      left,
      h('div', { className: 'pha-split' + (dragging ? ' drag' : ''), onPointerDown: startDrag, onPointerMove: dragMove, onPointerUp: endDrag, onPointerCancel: endDrag, onLostPointerCapture: endDrag }),
      right,
    ),
  )
}

// Cordis service inject. The `slots` service (provided by the harness's
// client-ui-renderer) must be present BEFORE `apply` runs, or `ctx.get('slots')`
// returns undefined and the PHA tab silently never registers. This static inject
// list is a DIFFERENT mechanism from the package.json `dsh.client.inject` array
// (which only orders bundle arrival) — cordis waits for the services listed here.
export const inject = ['slots']

function registerPhaView(scope) {
  const slots = scope.slots || scope.get('slots')
  if (!slots) return
  scope.effect(() => {
    const id = 'dsh-pha-css'
    if (typeof document !== 'undefined' && document.querySelector('style[data-pha]') === null) {
      const tag = document.createElement('style')
      tag.dataset.pha = id
      tag.textContent = CSS
      document.head.appendChild(tag)
    }
    return () => { const tag = document.querySelector('style[data-pha]'); if (tag) tag.remove() }
  })
  slots.inject('conversation.view', () => slots.register(
    { name: 'conversation.view', id: 'pha', order: 30, label: 'PHA' },
    PhaView,
  ))
}

export const apply = (ctx) => {
  // Never bail out when `slots` is not resolved yet: the static inject above makes
  // cordis wait for it, but if a loader hands us a scope where the service is still
  // arriving, `ctx.inject` opens a child scope that applies when it does. The old
  // `if (!slots) return` turned that race into a view that silently never appeared.
  ctx.inject(['slots'], (scope) => { registerPhaView(scope) })
}
