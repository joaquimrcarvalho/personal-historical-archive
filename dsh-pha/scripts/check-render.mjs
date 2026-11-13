#!/usr/bin/env node
// Render-level regression check for the PHA view.
//
//   node dsh-pha/scripts/check-render.mjs [source-file]
//
// `node --check` validates syntax only. The bug this check exists for was a *runtime*
// one: inline()'s parameter was `opts` while the inserted link code read `o.…`, so the
// first link token threw `ReferenceError: o is not defined` during render — and React
// unmounted the whole tab, leaving the PHA view blank while the slot stayed registered.
//
// It evaluates the real source with a stubbed React and DOM (no browser, no network),
// renders a note containing every link shape, and walks the produced tree.
import { readFileSync, readdirSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const sourcePath = process.argv[2] || join(root, 'src/client/index.js')

let failures = 0
const check = (cond, label) => {
  if (cond) console.log('  ok   ' + label)
  else { failures++; console.log('  FAIL ' + label) }
}

let src = readFileSync(sourcePath, 'utf8')
src = src.replace(/^import[^\n]*\n/gm, '')   // drop react + ./links.js imports
src = src.replace(/^export\s+/gm, '')        // export keywords (cordis face is re-attached by the build)

// Mirror scripts/build-client.mjs: the pure helpers live in their own dependency-free
// modules that the served bundle inlines, so inline every sibling module here too
// (a hard-coded list silently rots as soon as one is added).
const clientDir = dirname(sourcePath)
const inlined = readdirSync(clientDir)
  .filter((f) => f.endsWith('.js') && f !== basename(sourcePath))
  .sort()
  .map((f) => readFileSync(join(clientDir, f), 'utf8').replace(/^export\s+/gm, ''))
  .join('\n')
const runtime = 'let React = globalThis.React;'

const h = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) })
const sandbox = {
  React: { createElement: h, useState: (v) => [v, () => {}], useEffect: () => {}, useRef: (v) => ({ current: v }) },
  console,
  setTimeout,
  clearTimeout,
  document: {
    querySelector: () => null,
    getElementById: () => null,
    createElement: () => ({ dataset: {}, style: {} }),
    head: { appendChild: () => {} },
  },
  fetch: () => Promise.reject(new Error('no network in this check')),
}
sandbox.globalThis = sandbox
vm.createContext(sandbox)
vm.runInContext(`${runtime}\n${inlined}\n${src}\n;globalThis.__pha = { inline, renderMd, renderNote, classifyLink, parseWikilink, resolveNoteName, PhaView };`,
  sandbox, { filename: sourcePath })

const { renderNote, renderMd, PhaView } = sandbox.__pha

// The whole view, rendered with the stubbed hooks: this is what catches a typo in the
// pane/toolbar glue (an undefined helper, a mistyped state field) before it blanks the tab.
let view
try {
  view = PhaView()
  check(view && view.props && String(view.props.className).includes('pha-root'),
    'PhaView() renders the whole view (left pane, toolbar, right pane)')
} catch (e) {
  check(false, 'PhaView() renders the whole view — threw: ' + e.message)
}

// The view with an archive LOADED: the empty-state render above never reaches the
// document list or the reading pane, so those need their own render.
//
// - Each archived/list row must carry the "open the original" button, and pressing it
//   must ask `/pha/source` for exactly that target without also selecting the row.
// - The page image must carry the stable `pha serve` viewer link under it (the URL comes
//   from `pha page --json`, never rebuilt here), labelled so a reader knows the local
//   `pha serve` must be running; a page JSON without `page_url` must render no link.
const DOCS = [
  { id: 18, filename: 'schutte.pdf', dir_path: 'collections/schutte', kind: 'pdf', page_count: 3, status: 'done', path: '/a/dropbox/collections/schutte/schutte.pdf' },
  { id: 1, filename: '1576', dir_path: 'collections/cat', kind: 'dir', page_count: 4, status: 'done', path: '/a/dropbox/collections/cat/1576' },
]
const STATUS = {
  unscanned: [{ dir_path: 'collections/cat', count: 1, documents: ['new.pdf'] }],
  in_inbox: [{ dir_path: 'collections/park', count: 1, documents: ['parked.pdf'] }],
}
const PAGE_URL = 'http://127.0.0.1:8765/doc/schutte-schutte-pdf/p002'
const PAGE = {
  page_no: 2, variant: 'raw', text: 'page body', reviewed: false,
  page_file: '/a/library/page-002.md', page_url: PAGE_URL,
  overview_url: 'http://127.0.0.1:8765/doc/schutte-schutte-pdf/',
}

/** Render the whole view with a state as if the archive had loaded (the hooks are
 *  stubbed, so nothing fetches or mounts). `stateOverrides` replaces seed fields;
 *  `harness.searchText` types a query into the (otherwise empty) search box. */
function renderLoadedView(stateOverrides, harness) {
  const hx = harness || {}
  const fetched = []
  let stringSeen = 0
  const seed = Object.assign({
    docs: DOCS,
    status: STATUS,
    archive: '/a',
    selectedId: 18,
    detail: {
      doc: DOCS[0],
      pages: [{ id: 1, page_no: 1, status: 'done' }, { id: 2, page_no: 2, status: 'done' }],
      edits: [],
      matched: false,
    },
    pageReq: { doc: 18, page: 2, edited: false },
    page: PAGE,
  }, stateOverrides || {})
  const sandbox = {
    React: {
      createElement: h,
      useState: (init) => {
        const v = typeof init === 'function' ? init() : init
        // The view-state hook is the one whose initializer returns the seeded state.
        if (v && typeof v === 'object' && Object.prototype.hasOwnProperty.call(v, 'docs')) {
          return [Object.assign({}, v, seed), () => {}]
        }
        // The two boolean view toggles are initialized from functions: turn the image
        // pane on so the reading pane (and the link under the image) is rendered.
        if (typeof init === 'function' && typeof v === 'boolean') return [true, () => {}]
        // The first plain string hook is the search box: a check may type into it,
        // since a stubbed setState cannot.
        if (typeof v === 'string') {
          stringSeen += 1
          if (stringSeen === 1 && hx.searchText !== undefined) return [hx.searchText, () => {}]
        }
        return [v, () => {}]
      },
      useEffect: () => {},
      useRef: (v) => ({ current: v }),
    },
    console,
    setTimeout,
    clearTimeout,
    document: {
      querySelector: () => null,
      getElementById: () => null,
      createElement: () => ({ dataset: {}, style: {} }),
      head: { appendChild: () => {} },
    },
    fetch: (url) => { fetched.push(url); return Promise.resolve({ json: async () => ({ ok: true, path: '/a/x' }) }) },
  }
  sandbox.globalThis = sandbox
  vm.createContext(sandbox)
  vm.runInContext(`${runtime}\n${inlined}\n${src}\n;globalThis.__pha = { PhaView };`,
    sandbox, { filename: sourcePath })
  const view = sandbox.__pha.PhaView()
  const nodes = []
  const walk = (n) => {
    if (n === null || n === undefined || typeof n !== 'object') return
    if (Array.isArray(n)) { n.forEach(walk); return }
    if (n.type !== undefined) nodes.push(n)
    walk(n.children)
  }
  walk(view)
  return { nodes, fetched, seed }
}

try {
  const loaded = renderLoadedView()
  const { nodes, fetched } = loaded
  const openButtons = nodes.filter((n) => n.type === 'button'
    && String(n.props.className || '').includes('pha-open-src'))
  check(openButtons.length === 4,
    `every document list row has an "open original" button (2 archived + 1 new in dropbox + 1 parked) — got ${openButtons.length}`)
  let stopped = 0
  for (const b of openButtons) b.props.onClick({ stopPropagation() { stopped++ } })
  await new Promise((r) => setTimeout(r, 10))
  check(stopped === openButtons.length,
    'an "open" click does not also select the row it sits in')
  check(fetched.includes('/pha/source?doc=18'), 'an archived document opens by id')
  check(fetched.includes('/pha/source?under=dropbox&rel=collections%2Fcat%2Fnew.pdf'),
    'a "new in dropbox" item opens by its dropbox-relative path')
  check(fetched.includes('/pha/source?under=inbox&rel=collections%2Fpark%2Fparked.pdf'),
    'a parked inbox item opens by its inbox-relative path')

  const serves = nodes.filter((n) => n.type === 'a'
    && String(n.props.className || '').includes('pha-serve-link'))
  check(serves.length === 1, 'the page image carries one pha serve link')
  const serve = serves[0]
  check(!!serve && serve.props.href === PAGE_URL,
    'the pha serve link points at the page URL from `pha page --json` (never rebuilt here)')
  check(!!serve && serve.props.target === '_blank' && String(serve.props.rel).includes('noopener'),
    'the pha serve link opens in a new browser tab')
  check(!!serve && /pha serve/.test(String(serve.children.join(''))),
    'the link names the pha serve viewer')
  const imgAt = nodes.findIndex((n) => n.type === 'img')
  const linkAt = nodes.findIndex((n) => n === serve)
  check(imgAt >= 0 && linkAt > imgAt, 'the pha serve link sits under the image')
  const hints = nodes.filter((n) => String(n.props.className || '') === 'pha-serve-hint')
  check(hints.length === 1 && /must be running/.test(String(hints[0].children.join(''))),
    'the view states that pha serve must be running')

  // A page JSON without `page_url` (an older pha) must not render a dead link.
  const noUrl = renderLoadedView({ page: Object.assign({}, PAGE, { page_url: null }) })
  check(noUrl.nodes.filter((n) => n.type === 'a'
    && String(n.props.className || '').includes('pha-serve-link')).length === 0,
    'no pha serve link when the page JSON has no page_url')
} catch (e) {
  check(false, 'the loaded view renders and its "open"/serve actions work — threw: ' + e.message)
}

const NOTE = [
  '---',
  'title: Test note',
  '---',
  '# Heading one',
  '',
  'Wikilinks: [[Malaca]], [[Malaca|the strait]], [[Malaca#Trade]], [[malaca-note.md]] and [[missing-note]].',
  '',
  'Citations: [p. 437](http://127.0.0.1:8765/doc/colx-d/p437) and [pic](http://127.0.0.1:8765/doc/colx-d/p051.jpg).',
  '',
  // The embed form notes/README.md tells the owner to use for a picture that belongs
  // inline. Before the fix the `!` stayed as text and the rest became a plain link.
  'Embedded picture: ![](http://127.0.0.1:8765/doc/colx-d/p051.jpg) inline in a line.',
  '',
  '![a map](http://127.0.0.1:8765/doc/colx-d/p437.jpg)',
  '',
  'Other links: [sibling](other-note.md), [top](#Heading-one), [site](https://example.com/x).',
  '',
  'Reference [^1].',
  '',
  '[^1]: *Doc* (doc 1), p. 2 — [p. 2](http://127.0.0.1:8765/doc/colx-d/p2).',
  '',
].join('\n')

const clicks = []
const linkOpts = {
  onNote: (name, anchor) => clicks.push(['note', name, anchor]),
  onCitation: (c) => clicks.push(['cite', c.slug, c.page]),
  onAnchor: (id) => clicks.push(['anchor', id]),
  noteNames: ['Malaca', 'other-note'],
}

let tree
try {
  tree = renderNote(NOTE, linkOpts)
  check(true, 'renderNote() renders a note with every link shape (no throw)')
} catch (e) {
  check(false, 'renderNote() renders a note with every link shape (no throw) — threw: ' + e.message)
}

const nodes = []
const walk = (n, into) => {
  const sink = into || nodes
  if (n === null || n === undefined || typeof n !== 'object') return
  if (Array.isArray(n)) { n.forEach((c) => walk(c, sink)); return }
  if (n.type !== undefined) sink.push(n)
  walk(n.children, sink)
}
walk(tree)

const anchors = nodes.filter((n) => n.type === 'a')
const byClass = (c) => anchors.filter((n) => String(n.props.className || '').includes(c))
let prevented = 0
const click = (n, mods) => {
  if (n.props.onClick) n.props.onClick(Object.assign({ preventDefault() { prevented++ } }, mods || {}))
}

check(nodes.some((n) => n.type === 'h1' && n.props.id), 'headings carry an id (anchor targets)')
check(byClass('pha-note-link').length >= 5, 'wikilinks render as note links')
check(byClass('pha-note-link dangling').length === 2, 'both unresolved wikilinks ([[missing-note]], [[malaca-note.md]]) render as dangling')
check(byClass('pha-page-link').length === 3, 'citations (and the one in the footnote) render as page links')
check(byClass('pha-ext-link').length === 1, 'a non-citation URL is the only external link')
check(byClass('pha-ext-link').every((n) => n.props.target === '_blank' && String(n.props.rel).includes('noopener')),
  'external links open in a new tab with rel=noopener')
check(anchors.some((n) => n.props.href === '#Heading-one'), 'a same-page anchor keeps its href')

const alias = byClass('pha-note-link').find((n) => String(n.children.join('')) === 'the strait')
check(!!alias, '[[Malaca|the strait]] renders the alias as its label')
if (alias) { click(alias); }
const dangling = byClass('pha-note-link dangling').find((n) => String(n.children.join('')) === 'missing-note')
if (dangling) { click(dangling) }
// A citation link keeps the served URL and a plain click follows it (a new tab, like
// Obsidian); ⌘/Ctrl-click is what opens it in this view.
const citation = byClass('pha-page-link')[0]
const preventedBefore = prevented
if (citation) { click(citation) }                       // plain click: follow the URL
const plainFollowed = prevented === preventedBefore
if (citation) { click(citation, { metaKey: true }) }     // modifier: read it here
const anchorLink = anchors.find((n) => n.props.href === '#Heading-one')
if (anchorLink) { anchorLink.props.onClick({ preventDefault() {} }) }

check(JSON.stringify(clicks[0]) === JSON.stringify(['note', 'Malaca', null]),
  'clicking an alias wikilink asks for the note, not the alias')
check(JSON.stringify(clicks[1]) === JSON.stringify(['note', 'missing-note', null]),
  'clicking a dangling link still tries the note (the pane then explains why)')
check(byClass('pha-page-link').every((n) => n.props.target === '_blank' && String(n.props.rel).includes('noopener')),
  'a citation link opens in a new tab (its href is the served page)')
check(byClass('pha-page-link').every((n) => String(n.props.href).indexOf('/doc/') > 0),
  'a citation link keeps the real served URL')
check(plainFollowed, 'a plain click on a citation does NOT divert — the URL is followed')
check(JSON.stringify(clicks[2]) === JSON.stringify(['cite', 'colx-d', 437]),
  '⌘/Ctrl-clicking a citation opens that page in this view')
check(JSON.stringify(clicks[3]) === JSON.stringify(['anchor', 'Heading-one']),
  'clicking an anchor scrolls instead of navigating')

check(byClass('pha-fn-back').length === 1, 'each footnote links back to its reference')

// Markdown image embeds: the notes embed a page with `![]()` (an inline picture and a
// standalone one). They must become <img> elements — not a literal "!" followed by a
// link — and they must use the inline-image class, never the full-pane `.pha-img`.
const imgs = nodes.filter((n) => n.type === 'img')
check(imgs.length === 2, 'markdown image embeds render as <img> (not "!" + a link)')
check(imgs.every((n) => String(n.props.className || '').includes('pha-md-img')),
  'an embedded image uses the inline-image class, not the full-pane page image')
check(imgs.every((n) => !String(n.props.className || '').includes('pha-img')),
  'an embedded image does not reuse the page-pane <img> class')
check(imgs.some((n) => n.props.src === 'http://127.0.0.1:8765/doc/colx-d/p051.jpg'),
  'an embedded image keeps its src')
check(imgs.some((n) => n.props.alt === 'a map'), 'an embedded image keeps its alt text')
check(imgs.every((n) => String(n.props.loading) === 'lazy'), 'an embedded image is lazy-loaded')

// The view passes an `imageSrc` resolver: a picture that points at the pha serve viewer
// is then served by the plugin's own binary route (the slug is resolved against the
// archive being browsed), so a note's embed renders without `pha serve` running.
const localTree = renderNote('![](http://127.0.0.1:8765/doc/colx-d/p437.jpg)', {
  imageSrc: (c) => (c && c.page !== null && c.page !== undefined)
    ? '/pha/render?doc=9&page=' + c.page
    : null,
})
const localNodes = []
walk(localTree, localNodes)
const localImgs = localNodes.filter((n) => n.type === 'img')
check(localImgs.length === 1 && localImgs[0].props.src === '/pha/render?doc=9&page=437',
  'a pha-serve image embed is rewritten to the local render route')
check(localImgs.every((n) => String(n.props.className).includes('pha-md-img')),
  'the rewritten embed keeps the inline-image class')

// Definition bodies and edited pages render markdown with no link context at all: that
// path must not throw either (it is the one that would break on a missing `o`).
let plain
try {
  plain = renderMd('# Body\n\nA [link](https://example.com/y) and [[Some Note]].\n')
  check(true, 'renderMd() with no link context does not throw')
} catch (e) {
  check(false, 'renderMd() with no link context does not throw — threw: ' + e.message)
}
const plainNodes = []
walk(plain, plainNodes)
const plainExt = plainNodes.filter((n) => n.type === 'a')
check(plainExt.length >= 1 && plainExt.every((n) => n.props.target === '_blank'),
  'without a link context, links still default to a new tab')

// ---- encoded records (the pane's block, the document-row count, Records mode, and the
// search source selector). The records routes are stubbed like every fetch here, so
// these checks pin the RENDER shapes and the page/document a click targets — not the
// host route (that is part A).
const RECORD_DOC_ROWS = [
  { id: 101, document_id: 18, filename: 'schutte.pdf', collection: 'collections/schutte', path: '/a/dropbox/collections/schutte/schutte.pdf', page_no: 2, encoder: 'table', kind: 'entry', created_at: '2026-01-01', data: { text: 'Angola 1553', role: 'head' } },
  { id: 102, document_id: 18, filename: 'schutte.pdf', collection: 'collections/schutte', path: '/a/dropbox/collections/schutte/schutte.pdf', page_no: null, encoder: 'table', kind: 'entry', created_at: '2026-01-01', data: { name: 'orphan note' } },
]
const RECORD_ARCHIVE_ROWS = [
  { id: 201, document_id: 18, filename: 'schutte.pdf', collection: 'collections/schutte', path: '/a/x', page_no: 2, encoder: 'table', kind: 'entry', created_at: '2026-01-01', data: { text: 'Antwerp market' } },
  { id: 202, document_id: 1, filename: '1576', collection: 'collections/cat', path: '/a/y', page_no: null, encoder: 'index', kind: 'name', created_at: '2026-01-01', data: { name: 'Silva' } },
]
const textOf = (n) => {
  if (n === null || n === undefined) return ''
  if (typeof n !== 'object') return String(n)
  if (Array.isArray(n)) return n.map(textOf).join('')
  return textOf(n.children)
}
const hasClass = (n, c) => String((n.props && n.props.className) || '').split(/\s+/).includes(c)
const tick = () => new Promise((resolve) => setTimeout(resolve, 10))

try {
  // Default: the page list slot shows pages and the page context (image/readings)
  // is visible. Records are another way to browse the same document, not another
  // page-reader mode.
  const reading = renderLoadedView({ records: RECORD_DOC_ROWS, recordsTotal: 2 })
  check(reading.nodes.filter((n) => hasClass(n, 'pha-rec')).length === 0,
    'opening a document shows the page list, not the records list')
  check(reading.nodes.some((n) => hasClass(n, 'pha-pages')), 'the page list is visible by default')
  check(reading.nodes.some((n) => hasClass(n, 'pha-content')), 'the page context is visible by default')
  const pagesTab = reading.nodes.find((n) => n.type === 'button'
    && /browse this document by page/.test(String((n.props && n.props.title) || '')))
  const recordsTab = reading.nodes.find((n) => n.type === 'button'
    && /browse this document by its encoder records/.test(String((n.props && n.props.title) || '')))
  check(!!pagesTab && textOf(pagesTab) === 'Pages', 'the list slot has a "Pages" tab')
  check(!!recordsTab && textOf(recordsTab) === 'Records(2)',
    'the list slot has a "Records (N)" tab carrying the document total')
  const indexPanel = reading.nodes.find((n) => hasClass(n, 'pha-index'))
  check(!!indexPanel && /^0 0 \d+%$/.test(String((indexPanel.props && indexPanel.props.style && indexPanel.props.style.flex) || '')),
    'the page-list / records panel has an allocated height (not a collapsed flex item)')
  check(reading.nodes.some((n) => hasClass(n, 'pha-hsplit') && n.props.onPointerDown),
    'a draggable splitter separates the list panel from the reading pane')

  const pane = renderLoadedView({ records: RECORD_DOC_ROWS, recordsTotal: 2, recordOpen: 101, listMode: 'records' })
  check(pane.nodes.filter((n) => hasClass(n, 'pha-pages')).length === 0,
    'the Records list replaces the page list (not the page reader)')
  check(pane.nodes.some((n) => hasClass(n, 'pha-content')),
    'the page context (image / raw / edited) stays visible while browsing records')
  check(pane.nodes.some((n) => hasClass(n, 'pha-rec-browser')),
    'the record list is a bounded, scrollable browser in the page-list slot')
  const recRows = pane.nodes.filter((n) => hasClass(n, 'pha-rec'))
  check(recRows.length === 2, 'the document Records list renders one row per encoded record')
  check(pane.nodes.some((n) => hasClass(n, 'pha-rec-encoder') && textOf(n) === 'table'),
    'records are grouped by encoder')
  check(pane.nodes.some((n) => hasClass(n, 'pha-rec-tag') && textOf(n) === '[table/entry]'),
    'a record row shows its encoder/kind')
  check(pane.nodes.filter((n) => n.type === 'pre' && hasClass(n, 'pha-pre') && textOf(n).includes('Angola 1553')).length === 0,
    'the index list does not expand record JSON (selection shows it in the page pane)')
  check(pane.nodes.filter((n) => n.type === 'button' && textOf(n) === 'open page').length === 0,
    'record rows have no separate "open page" button (selecting is enough)')
  // Selecting a page-grounded record reads the page's records for the page pane.
  const firstRow = pane.nodes.filter((n) => hasClass(n, 'pha-rec'))[0]
  await firstRow.props.onClick()
  await tick()
  check(pane.fetched.includes('/pha/records?doc=18&page=2&limit=100'),
    'selecting a record loads the records for its page into the page pane')
} catch (e) { check(false, 'the per-document view tabs between page reader and records — threw: ' + e.message) }

try {
  // The page pane's third view: records that start on the selected page. Two records on
  // one page must BOTH render, with the selected one highlighted; the index list is
  // separate and keeps no records JSON.
  const REC_PAGE = [
    { id: 101, document_id: 18, filename: 'schutte.pdf', page_no: 2, encoder: 'apparatus', kind: 'section', data: { text: 'DOCUMENTAÇÃO', section_kind: 'title' } },
    { id: 102, document_id: 18, filename: 'schutte.pdf', page_no: 2, encoder: 'documents', kind: 'document', data: { text: 'CARTA DO IRMÃO', from: 'Aleixo Dias', place: 'Cochim' } },
  ]
  const rp = renderLoadedView({ textMode: 'record', selectedRecordId: 101, pageRecords: REC_PAGE, pageRecordsLoading: false })
  const cards = rp.nodes.filter((n) => hasClass(n, 'pha-record'))
  check(cards.length === 2, 'the page pane shows BOTH records starting on the page')
  check(cards.some((n) => hasClass(n, 'sel') && textOf(n).includes('DOCUMENTAÇÃO')),
    'the record selected in the index is highlighted in the page pane')
  check(textOf(rp.nodes.find((n) => hasClass(n, 'pha-text'))) .includes('2 records for this page'),
    'the record pane states how many records start on the page')
  const recButton = rp.nodes.find((n) => n.type === 'button' && textOf(n) === 'Record')
  check(!!recButton && hasClass(recButton, 'primary'), 'the Record view is a third page-pane button (Raw / Edited / Record)')
  check(rp.nodes.filter((n) => n.type === 'pre' && hasClass(n, 'pha-pre') && textOf(n).includes('Angola')).length === 0,
    'the index list keeps no inline JSON while the page pane shows records')
} catch (e) { check(false, 'the page pane renders the page records — threw: ' + e.message) }

try {
  const counted = renderLoadedView({ recordCounts: { 18: 3 } })
  const metas = counted.nodes.filter((n) => hasClass(n, 'pha-doc-meta')).map(textOf)
  const recChips = counted.nodes.filter((n) => hasClass(n, 'pha-chip') && hasClass(n, 'rec')).map(textOf)
  check(metas.includes('3p'), 'a document row keeps its page meta')
  check(recChips.includes('3r'), 'a document row shows a colored "Nr" record-count chip')
  check(metas.includes('4p'), 'a document without records keeps its plain meta')
} catch (e) { check(false, 'the document row shows its record count — threw: ' + e.message) }

try {
  const rm = renderLoadedView({
    recordMode: true,
    records: RECORD_ARCHIVE_ROWS,
    recordsTotal: 2,
    recordsOffset: 0,
    recordsLimit: 25,
    recordFilters: { encoder: '', kind: '', field: '', ignoreCase: false },
    status: Object.assign({}, STATUS, { records: { total: 2, by_encoder: { table: 1, index: 1 } } }),
  })
  const hdr = rm.nodes.find((n) => String((n.props && n.props.className) || '') === 'pha-group-h' && /encoded records/i.test(textOf(n)))
  check(!!hdr && /\(2\)/.test(textOf(hdr)), 'Records mode shows a header with the total')
  const encoderSelect = rm.nodes.find((n) => n.type === 'select' && n.children.some((c) => c && c.props && c.props.value === 'table'))
  check(!!encoderSelect, 'the encoder filter is a select built from status.records.by_encoder')
  check(rm.nodes.some((n) => n.type === 'input' && n.props && n.props.type === 'checkbox'),
    'the ignore-case filter is a checkbox')
  check(rm.nodes.some((n) => n.type === 'input' && n.props && String(n.props.placeholder) === 'field NAME=VALUE'),
    'the field filter is a NAME=VALUE input')
  check(rm.nodes.some((n) => n.type === 'select' && String(n.props.value) === '25' && n.props.title === 'records per page'),
    'the limit selector is present')
  const recordsButton = rm.nodes.find((n) => n.type === 'button' && /^Records/.test(textOf(n)))
  check(!!recordsButton && textOf(recordsButton) === 'Records (2)',
    'the toolbar "Records (N)" button carries the archive total from pha status')
  const archiveRows = rm.nodes.filter((n) => hasClass(n, 'pha-rec'))
  check(archiveRows.length === 2, 'Records mode renders a row per record')
  const pageRow = archiveRows.find((n) => textOf(n).includes('schutte.pdf'))
  const nullRow = archiveRows.find((n) => textOf(n).includes('1576'))
  check(!!pageRow && textOf(pageRow).includes('[table/entry]') && textOf(pageRow).includes('p.2') && textOf(pageRow).includes('Antwerp market'),
    'a record row shows document, encoder/kind, page and text')
  check(!!nullRow && textOf(nullRow).includes('not page-grounded'), 'a null page renders "not page-grounded", not a link')
  check(rm.nodes.filter((n) => n.type === 'a').every((n) => !textOf(n).includes('1576')),
    'no dead link is created for a record without a page')
  await pageRow.props.onClick()
  await tick()
  check(rm.fetched.includes('/pha/document?doc=18') && rm.fetched.includes('/pha/page?doc=18&page=2'),
    'clicking a record row opens its document and lands on its page')
  await nullRow.props.onClick()
  await tick()
  check(rm.fetched.includes('/pha/document?doc=1'), 'clicking a page-less record row still opens its document')
  check(!rm.fetched.some((u) => { const uu = String(u); return uu === '/pha/page?doc=1' || uu.indexOf('/pha/page?doc=1&') === 0 }),
    'a page-less record opens no page (no dead link)')
} catch (e) { check(false, 'Records mode renders filters, rows and page targeting — threw: ' + e.message) }

try {
  const mixed = renderLoadedView({
    searchMode: true,
    searchSource: 'all',
    hits: [
      { document_id: 18, filename: 'schutte.pdf', collection: 'collections/schutte', page_no: 2, variant: 'raw', text: 'page hit' },
      { kind: 'record', record_id: 201, encoder: 'table', record_kind: 'entry', document_id: 18, filename: 'schutte.pdf', collection: 'collections/schutte', page_no: 2, text: 'Angola record text' },
    ],
  })
  const srcSelect = mixed.nodes.find((n) => n.type === 'select' && n.children.some((c) => c && c.props && c.props.value === 'all'))
  check(!!srcSelect && String(srcSelect.props.value) === 'all',
    'the search line carries a source selector (pages / records / all)')
  check(mixed.nodes.some((n) => hasClass(n, 'pha-chip') && textOf(n) === 'record'),
    'a record hit carries a "record" chip')
  const hitRow = mixed.nodes.find((n) => hasClass(n, 'pha-doc') && textOf(n).includes('Angola record text'))
  check(!!hitRow && textOf(hitRow).includes('[table/entry]'), 'a record hit shows its encoder/kind')
  await hitRow.props.onClick()
  await tick()
  check(mixed.fetched.includes('/pha/document?doc=18') && mixed.fetched.includes('/pha/page?doc=18&page=2'),
    'clicking a record hit opens the same document/page as a page hit')
  const typed = renderLoadedView({ searchSource: 'records' }, { searchText: 'angola' })
  const searchButton = typed.nodes.filter((n) => n.type === 'button' && textOf(n) === 'Search')[0]
  await searchButton.props.onClick()
  await tick()
  check(typed.fetched.includes('/pha/search?q=angola&limit=20&source=records'),
    'Search sends the chosen source to /pha/search')
} catch (e) { check(false, 'search renders record hits and sends the source — threw: ' + e.message) }

console.log('')
if (failures > 0) {
  console.log(`${failures} check(s) FAILED`)
  process.exit(1)
}
console.log('all render checks passed')
