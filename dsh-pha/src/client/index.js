// dsh-pha client module — the PHA conversation view.
//
// This is SOURCE. The deployment's `dev:web` client build bundles it into
// `lib/client.js` (discovered through the package.json `dsh.client` block) and
// registers it into the browser. It is not built here.
//
// Data path: same-origin `/pha/*` JSON endpoints registered by the host half
// (lib/index.js). No dynamic-plugin RPC. Mutations go through the pha_* tools.
import React from 'react'

async function get(path) {
  const res = await fetch(path)
  return await res.json()
}

// ---- compact markdown -> React -------------------------------------------
function inline(t) {
  const out = []
  const re = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`|~~[^~\n]+~~|\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m
  while ((m = re.exec(t))) {
    if (m.index > last) out.push(t.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**') && tok.endsWith('**')) out.push(React.createElement('strong', null, tok.slice(2, -2)))
    else if (tok.startsWith('__') && tok.endsWith('__')) out.push(React.createElement('strong', null, tok.slice(2, -2)))
    else if (tok.startsWith('`') && tok.endsWith('`')) out.push(React.createElement('code', null, tok.slice(1, -1)))
    else if (tok.startsWith('~~') && tok.endsWith('~~')) out.push(React.createElement('del', null, tok.slice(2, -2)))
    else if (tok.startsWith('[')) {
      const nm = tok.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
      out.push(nm ? React.createElement('a', { href: nm[2] }, nm[1]) : tok)
    } else if ((tok.startsWith('*') && tok.endsWith('*')) || (tok.startsWith('_') && tok.endsWith('_'))) {
      out.push(React.createElement('em', null, tok.slice(1, -1)))
    } else out.push(tok)
    last = re.lastIndex
  }
  if (last < t.length) out.push(t.slice(last))
  return out
}

function renderMd(text) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks = []
  let i = 0
  let para = []
  const flush = () => {
    if (para.length) { blocks.push(React.createElement('p', null, inline(para.join(' ')))); para = [] }
  }
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()
    if (trimmed === '') { flush(); i++; continue }
    if (trimmed.startsWith('```')) {
      flush()
      const code = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) { code.push(lines[i]); i++ }
      i++
      blocks.push(React.createElement('pre', { className: 'pha-md-code' }, code.join('\n')))
      continue
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { flush(); blocks.push(React.createElement('hr')); i++; continue }
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/)
    if (h) { flush(); blocks.push(React.createElement('h' + Math.min(6, h[1].length), null, inline(h[2]))); i++; continue }
    const bq = trimmed.match(/^>\s?(.*)$/)
    if (bq) { flush(); blocks.push(React.createElement('blockquote', null, inline(bq[1]))); i++; continue }
    const ul = trimmed.match(/^([-*+])\s+(.*)$/)
    const ol = trimmed.match(/^(\d+)[.)]\s+(.*)$/)
    if (ul || ol) {
      flush()
      const isOl = !ul
      const items = []
      while (i < lines.length) {
        const ln = lines[i].trim()
        const m1 = ln.match(/^([-*+])\s+(.*)$/)
        const m2 = ln.match(/^(\d+)[.)]\s+(.*)$/)
        if (isOl && m2) { items.push(m2[2]); i++; continue }
        if (!isOl && m1) { items.push(m1[2]); i++; continue }
        break
      }
      blocks.push(React.createElement(isOl ? 'ol' : 'ul', null, items.map((it, k) => React.createElement('li', { key: k }, inline(it)))))
      continue
    }
    para.push(trimmed)
    i++
  }
  flush()
  return blocks
}

const CSS = [
  '.pha-root{display:flex;flex-direction:column;height:100%;min-height:0;overflow:hidden;font-size:13px;background:var(--dsw-alias-bg-base,transparent);color:var(--dsw-alias-label-primary,inherit)}',
  '.pha-top{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--dsw-alias-border-l1,#333)}',
  '.pha-title{font-weight:600;font-size:13px}', '.pha-muted{opacity:.6;font-size:12px}',
  '.pha-main{flex:1;display:flex;min-height:0;overflow:hidden}',
  '.pha-left{min-width:200px;max-width:72%;overflow:auto;padding:8px;flex:0 0 auto}',
  '.pha-search{display:flex;gap:6px;align-items:center;padding:0 0 6px;border-bottom:1px solid var(--dsw-alias-border-l1,#333);position:sticky;top:0;background:var(--dsw-alias-bg-base,transparent);z-index:2}',
  '.pha-split{width:6px;flex:0 0 6px;cursor:col-resize;background:var(--dsw-alias-border-l1,#333);touch-action:none}',
  '.pha-split:hover,.pha-split.drag{background:var(--dsw-alias-brand-primary,#0b5fff)}',
  '.pha-right{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:10px}',
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
  '.pha-btn.primary{border-color:transparent;background:var(--dsw-alias-brand-primary,#0b5fff);color:#fff}',
  '.pha-btn.on{border-color:transparent;background:var(--dsw-alias-state-warn-primary,#e8890c);color:#fff}',
  '.pha-btn.small{padding:1px 7px;font-size:11px}',
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
  '.pha-empty{padding:20px;text-align:center;opacity:.5}',
  '.pha-err{color:var(--dsw-alias-state-error-primary,#e03131);padding:6px 10px;font-size:12px}',
].join('')

function statusClass(s) {
  s = String(s || '')
  if (s === 'done' || s === 'ok') return 'ok'
  if (s === 'error') return 'bad'
  if (s === 'processing' || s === 'running' || s === 'starting' || s === 'pending') return 'busy'
  return 'dim'
}

function PhaView() {
  const h = React.createElement
  const [state, setState] = React.useState({ docs: null, docsErr: null, archive: null, selectedId: null, detail: null, hits: null, query: '', searchMode: false, page: null, pageReq: null })
  const [searchText, setSearchText] = React.useState('')
  const [jump, setJump] = React.useState('')
  const [range, setRange] = React.useState(1)
  const [leftPct, setLeftPct] = React.useState(38)
  const [dragging, setDragging] = React.useState(false)
  const [showImg, setShowImg] = React.useState(false)
  const [imgData, setImgData] = React.useState(null)
  const [plainShow, setPlainShow] = React.useState(false)

  React.useEffect(() => {
    get('/pha/documents').then((r) => setState((s) => ({ ...s, docs: r && r.ok ? r.documents : null, docsErr: r && r.ok ? null : (r && r.error) || 'documents call failed', archive: r && r.archive || s.archive }))).catch((e) => setState((s) => ({ ...s, docsErr: String((e && e.message) || e) })))
  }, [])

  const pageNo = state.pageReq ? state.pageReq.page : null
  React.useEffect(() => {
    if (!showImg || !state.pageReq) { setImgData(null); return }
    let alive = true
    get('/pha/pageImage?doc=' + encodeURIComponent(state.pageReq.doc) + '&page=' + state.pageReq.page).then((r) => { if (alive) setImgData(r && r.ok ? r.dataUrl : null) }).catch(() => { if (alive) setImgData(null) })
    return () => { alive = false }
  }, [showImg, pageNo])

  async function openDoc(id) {
    setState((s) => ({ ...s, selectedId: id, page: null, pageReq: null }))
    const r = await get('/pha/document?doc=' + encodeURIComponent(id))
    setState((s) => ({ ...s, detail: r && r.ok ? { doc: r.doc, pages: r.pages || [], edits: r.edits || [], matched: false } : null }))
  }
  async function openSearchDoc(id) {
    const r = await get('/pha/document?doc=' + encodeURIComponent(id))
    const matched = (state.hits || []).filter((hit) => hit.document_id === id)
    const vmap = {}
    matched.forEach((hit) => { vmap[hit.page_no] = hit.variant })
    const pages = r && r.ok && r.pages ? r.pages.filter((p) => vmap[p.page_no] !== undefined) : []
    const s = state
    setState((x) => ({ ...x, selectedId: id, detail: r && r.ok ? { doc: r.doc, pages, edits: r.edits || [], matched: true } : null, searchPageVariant: vmap, page: null, pageReq: null }))
    void s
  }
  async function openPage(pageNo, edited) {
    const doc = state.selectedId
    setState((s) => ({ ...s, pageReq: { doc, page: pageNo, edited: !!edited } }))
    const r = await get('/pha/page?doc=' + encodeURIComponent(doc) + '&page=' + pageNo + (edited ? '&edited=1' : ''))
    setState((s) => ({ ...s, page: r && r.ok ? r.page : null }))
  }
  async function doSearch() {
    const q = searchText.trim()
    if (!q) return
    setState((s) => ({ ...s, searchMode: true, hits: null, selectedId: null, detail: null, page: null, pageReq: null }))
    const r = await get('/pha/search?q=' + encodeURIComponent(q) + '&limit=20')
    setState((s) => ({ ...s, hits: r && r.ok ? r.results : null }))
  }
  function clearSearch() { setSearchText(''); setState((s) => ({ ...s, searchMode: false, hits: null, selectedId: null, detail: null, page: null, pageReq: null })) }

  const s = state
  const searchMode = !!s.searchMode
  const editors = []
  let groups = []
  if (searchMode && s.hits) {
    const byColl = {}
    for (const hit of s.hits) {
      const key = hit.collection || '(search)'
      ;(byColl[key] = byColl[key] || {})[hit.document_id] = { id: hit.document_id, filename: hit.filename, count: ((byColl[key][hit.document_id] && byColl[key][hit.document_id].count) || 0) + 1 }
    }
    for (const key of Object.keys(byColl).sort()) groups.push({ key, docs: Object.values(byColl[key]) })
  } else if (s.docs) {
    const map = {}
    for (const d of s.docs) { const key = d.dir_path || '(root)'; (map[key] = map[key] || []).push(d) }
    for (const key of Object.keys(map).sort()) groups.push({ key, docs: map[key] })
  }
  if (s.detail && s.detail.edits) {
    const seen = {}
    for (const e of s.detail.edits) if (!seen[e.editor]) { seen[e.editor] = true; editors.push(e.editor) }
  }

  function startDrag(e) { e.currentTarget.setPointerCapture(e.pointerId); setDragging(true) }
  function dragMove(e) {
    if (!dragging) return
    const par = e.currentTarget.parentElement
    const rect = par ? par.getBoundingClientRect() : null
    if (!rect || rect.width === 0) return
    const pct = ((e.clientX - rect.left) / rect.width) * 100
    setLeftPct(Math.min(66, Math.max(16, Math.round(pct))))
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
    h('button', { className: 'pha-btn', onClick: () => { if (s.searchMode) clearSearch(); get('/pha/documents').then((r) => setState((x) => ({ ...x, docs: r && r.ok ? r.documents : null }))) } }, '⟳ Refresh'),
  )
  const searchHeader = h('div', { className: 'pha-search' },
    h('input', { className: 'pha-input', placeholder: 'search the archive…', value: searchText, onChange: (e) => setSearchText(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') doSearch() } }),
    h('button', { className: 'pha-btn primary', onClick: doSearch }, 'Search'),
    searchMode ? h('button', { className: 'pha-btn small', title: 'clear search', onClick: clearSearch }, '✕') : null,
  )
  const left = h('div', { className: 'pha-left', style: { width: leftPct + '%' } },
    searchHeader,
    s.docsErr ? h('div', { className: 'pha-err' }, s.docsErr) : null,
    !s.docs && !s.docsErr ? h('div', { className: 'pha-empty' }, 'Loading documents…') : null,
    s.docs ? groups.map((g) => h('div', { className: 'pha-group', key: g.key },
      h('div', { className: 'pha-group-h' }, g.key + '  (' + g.docs.length + ')'),
      g.docs.map((d) => {
        const isSel = s.selectedId === d.id
        if (searchMode) {
          return h('div', { className: 'pha-doc' + (isSel ? ' sel' : ''), key: d.id, onClick: () => openSearchDoc(d.id) },
            h('span', { className: 'pha-chip busy' }, d.count + ' hit' + (d.count > 1 ? 's' : '')),
            h('span', { className: 'pha-doc-name', title: d.filename }, d.filename),
          )
        }
        return h('div', { className: 'pha-doc' + (isSel ? ' sel' : ''), key: d.id, onClick: () => openDoc(d.id) },
          h('span', { className: 'pha-chip ' + statusClass(d.status) }, d.status || '?'),
          h('span', { className: 'pha-doc-name', title: d.filename }, d.filename),
          h('span', { className: 'pha-doc-meta' }, (d.page_count || 0) + 'p' + (d.palaeographer ? ' · ' + d.palaeographer : '')),
        )
      }),
    )) : null,
  )

  let right
  if (!s.detail) right = h('div', { className: 'pha-empty' }, searchMode ? 'Select a matching document to see its matched pages.' : 'Select a document to read its pages.')
  else {
    const d = s.detail.doc
    const totalPages = d.page_count || Math.max(1, (s.detail.pages || []).length)
    const curPage = s.pageReq ? s.pageReq.page : null
    const selIsEdited = !!(s.pageReq && s.pageReq.edited)
    const effRange = (curPage && curPage >= 1 && curPage <= totalPages) ? curPage : range
    const pv = s.page
    const listPages = s.detail.pages || []
    const textBlock = pv ? h('div', { className: 'pha-text' },
      h('div', { className: 'pha-muted' }, 'page ' + (s.pageReq ? s.pageReq.page : '') + ' · ' + (pv.variant || '') + (pv.reviewed ? ' · reviewed' : '')),
      h('div', { className: 'pha-muted' }, pv.page_file || ''),
      plainShow ? h('pre', { className: 'pha-pre' }, pv.text || '(empty)') : h('div', { className: 'pha-md' }, renderMd(pv.text || '')),
    ) : null
    const mediaBlock = showImg ? h('div', { className: 'pha-media' }, imgData ? h('img', { className: 'pha-img', src: imgData, alt: 'page' }) : h('div', { className: 'pha-empty' }, 'No render image')) : null

    right = h('div', { className: 'pha-right' },
      h('div', null, h('strong', null, '#' + d.id + ' ' + d.filename), h('div', { className: 'pha-muted' }, (d.path || '') + ' · ' + (d.kind || ''))),
      h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' } },
        h('span', { className: 'pha-chip ' + statusClass(d.status) }, d.status || '?'),
        h('span', { className: 'pha-chip' }, (d.page_count || 0) + ' pages'),
        h('span', { className: 'pha-chip' }, d.palaeographer ? 'pal ' + d.palaeographer : 'no palaeographer'),
        h('span', { className: 'pha-chip' }, d.editor ? 'ed ' + d.editor : 'no editor'),
      ),
      h('div', { className: 'pha-pages' },
        h('div', { className: 'pha-jump' },
          h('span', { className: 'pha-muted' }, (searchMode ? listPages.length + ' matched / ' : '') + totalPages + ' pages'),
          h('input', { className: 'pha-num', type: 'number', min: 1, max: totalPages, value: jump, placeholder: 'page #', onChange: (e) => setJump(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') goTo(Number(jump)) } }),
          h('button', { className: 'pha-btn small', onClick: () => goTo(Number(jump)) }, 'Go'),
          h('input', { className: 'pha-range', type: 'range', min: 1, max: totalPages, value: effRange, onChange: (e) => { const v = Number(e.target.value); setRange(v); openPage(v, selIsEdited) } }),
          h('span', { className: 'pha-muted' }, effRange + ' / ' + totalPages),
        ),
        listPages.length ? h('div', { className: 'pha-strip' }, listPages.map((p) => h('span', {
          className: 'pha-page' + (curPage === p.page_no ? ' sel' : ''),
          key: p.id,
          title: 'status: ' + (p.status || '?') + (p.error ? ' · ' + p.error : ''),
          onClick: () => { const dv = (searchMode && s.searchPageVariant) ? (s.searchPageVariant[p.page_no] === 'edited') : selIsEdited; openPage(p.page_no, dv); setRange(p.page_no) },
        }, p.page_no))) : h('div', { className: 'pha-muted' }, searchMode ? 'No matched pages…' : 'No pages…'),
      ),
      h('div', { style: { display: 'flex', gap: 6, alignItems: 'center' } },
        h('button', { className: 'pha-btn small' + (selIsEdited ? '' : ' primary'), onClick: () => { if (s.pageReq) openPage(s.pageReq.page, false) } }, 'raw'),
        h('button', { className: 'pha-btn small' + (selIsEdited ? ' primary' : ''), onClick: () => { if (editors.length && s.pageReq) openPage(s.pageReq.page, true) } }, editors.length ? 'edited (' + editors.join(',') + ')' : 'edited'),
        h('button', { className: 'pha-btn small' + (showImg ? ' on' : ''), onClick: () => setShowImg(!showImg) }, 'image'),
        h('button', { className: 'pha-btn small' + (plainShow ? ' on' : ''), onClick: () => setPlainShow(!plainShow) }, plainShow ? 'md' : 'txt'),
      ),
      h('div', { className: 'pha-content' }, mediaBlock, textBlock),
    )
  }

  return h('div', { className: 'pha-root' },
    top,
    h('div', { className: 'pha-main' },
      left,
      h('div', { className: 'pha-split', onPointerDown: startDrag, onPointerMove: dragMove, onPointerUp: endDrag, onPointerCancel: endDrag }),
      right,
    ),
  )
}

export default {
  apply(ctx) {
    const slots = ctx.get('slots')
    if (!slots) return
    slots.inject('conversation.view', () => slots.register(
      { name: 'conversation.view', id: 'pha', order: 30, label: 'PHA' },
      PhaView,
    ))
  },
}
