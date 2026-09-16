#!/usr/bin/env node
// Unit checks for the PHA view's reading-position memory.
//
//   node dsh-pha/scripts/check-view.mjs
//
// The view is unmounted when the reader switches to the Chat tab, so the document and
// page they were on have to survive outside React state (src/client/viewmemory.js holds
// the rules; scripts/build-client.mjs inlines them into the served bundle). These checks
// pin the round trip: what is remembered, what is seeded back synchronously, and what
// still has to be loaded.
import { askContext, mergeDraft } from '../src/client/askcontext.js'
import { docNumberLabel, parseNumberQuery } from '../src/client/query.js'
import { scanRequestText, statusInbox, statusUnscanned } from '../src/client/statusview.js'
import { restorePlan, seedState, seedUi, snapshotView } from '../src/client/viewmemory.js'

let failures = 0
const eq = (actual, expected, label) => {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a === e) console.log('  ok   ' + label)
  else { failures++; console.log('  FAIL ' + label + '\n         got      ' + a + '\n         expected ' + e) }
}

const DEFAULTS = {
  docs: null, selectedId: null, detail: null, hits: null, searchMode: false,
  selectedNote: null, noteMode: false, page: null, pageReq: null,
  selectedDef: null, defMode: false, inboxSel: null, plainOverride: null,
}

console.log('snapshotView — what a reading position is')
const docState = Object.assign({}, DEFAULTS, { selectedId: 22, pageReq: { doc: 22, page: 437, edited: true }, plainOverride: true })
const docSnap = snapshotView(docState, { textOn: true, showImg: true, leftPct: 52 })
eq(docSnap.doc, { id: 22, page: 437, edited: true }, 'document + page + variant')
eq(docSnap.ui, { textOn: true, showImg: true, leftPct: 52 }, 'toggles and splitter width')
eq(docSnap.plain, true, 'an explicit txt/md override')
eq(snapshotView(DEFAULTS, {}).doc, null, 'no selection -> no document')
eq(snapshotView(DEFAULTS, {}).ui, { textOn: true, showImg: false, leftPct: 38 }, 'defaults when nothing was set')

const searchState = Object.assign({}, DEFAULTS, { searchMode: true, query: 'malaca', hits: [{ document_id: 1 }], searchPageVariant: { 3: 'edited' } })
eq(snapshotView(searchState, {}).search, { query: 'malaca', hits: [{ document_id: 1 }], pageVariant: { 3: 'edited' } }, 'search results')
eq(snapshotView(Object.assign({}, DEFAULTS, { noteMode: true, selectedNote: { name: 'malaca' } }), {}).note, 'malaca', 'an open note')
eq(snapshotView(Object.assign({}, DEFAULTS, { defMode: true, selectedDef: { path: '/a/b/editor.md' } }), {}).def.path, '/a/b/editor.md', 'an open definition')
eq(snapshotView(Object.assign({}, DEFAULTS, { inboxSel: { kind: 'collection', rel_path: 'collections/CAT' } }), {}).inbox.rel_path, 'collections/CAT', 'an inbox selection')

console.log('seedState — the identity comes back without waiting for a fetch')
const seeded = seedState(DEFAULTS, docSnap)
eq(seeded.selectedId, 22, 'document id seeded')
eq(seeded.pageReq, { doc: 22, page: 437, edited: true }, 'page request seeded (so the page reloads)')
eq(seeded.plainOverride, true, 'view override seeded')
eq(seedState(DEFAULTS, snapshotView(searchState, {})).searchMode, true, 'search mode seeded')
eq(seedState(DEFAULTS, snapshotView(searchState, {})).hits.length, 1, 'search hits seeded (no re-run)')
eq(seedState(DEFAULTS, snapshotView(Object.assign({}, DEFAULTS, { noteMode: true, selectedNote: { name: 'x' } }), {})).noteMode, true, 'note mode seeded')
eq(seedState(DEFAULTS, snapshotView(Object.assign({}, DEFAULTS, { defMode: true, selectedDef: { path: '/p.md' } }), {})).defMode, true, 'definition mode seeded')
eq(seedState(DEFAULTS, null).selectedId, null, 'no memory -> untouched defaults')
eq(seedState(DEFAULTS, docSnap).docs, null, 'content is never cached — only the selection')

console.log('seedUi — toggles survive too')
eq(seedUi(docSnap), { textOn: true, showImg: true, leftPct: 52 }, 'seeded from the snapshot')
eq(seedUi(null), { textOn: true, showImg: false, leftPct: 38 }, 'defaults without a snapshot')
eq(seedUi({ ui: { textOn: false } }).textOn, false, '"text off" is remembered, not defaulted to on')

console.log('restorePlan — what still has to be loaded')
eq(restorePlan(docSnap), { kind: 'document', id: 22, page: 437, edited: true }, 'a document needs its detail + page')
eq(restorePlan(snapshotView(Object.assign({}, DEFAULTS, { noteMode: true, selectedNote: { name: 'malaca' } }), {})),
  { kind: 'note', name: 'malaca' }, 'a note needs its content')
eq(restorePlan(snapshotView(Object.assign({}, DEFAULTS, { defMode: true, selectedDef: { path: '/p.md' } }), {})),
  { kind: 'definition', path: '/p.md' }, 'a definition needs its content')
eq(restorePlan(snapshotView(searchState, {})), null, 'a restored search needs nothing (its hits are in the snapshot)')
eq(restorePlan(null), null, 'no memory -> nothing to load')
const both = Object.assign({}, searchState, { selectedId: 7, pageReq: { doc: 7, page: 2, edited: false } })
eq(restorePlan(snapshotView(both, {})), { kind: 'document', id: 7, page: 2, edited: false },
  'a document opened from search results is the reading position')

console.log('askContext — what a question from the reading pane carries')
const ctx = askContext({ docId: 19, filename: 'DOCUMENTA-INDICA-1550-1553.pdf', page: 496, variant: 'edited', editor: 'latin-to-english@x' })
eq(ctx.split('\n')[0], 'Context: DOCUMENTA-INDICA-1550-1553.pdf (doc 19), page 496 — edited (latin-to-english@x) variant',
  'names the document, page and the exact variant being read')
eq(ctx.includes('`pha page 19 496 --edited`'), true, 'tells the agent how to recover the full page (edited)')
eq(askContext({ docId: 19, filename: 'x.pdf', page: 3, variant: 'raw' }).includes('`pha page 19 3`'), true,
  'the raw variant cites the plain page command')
eq(askContext({ docId: 22, filename: 'v.pdf', page: 437, variant: 'edited', reference: 'Rego, *Documentação* (1947)' }).split('\n')[1],
  'Reference: Rego, *Documentação* (1947)', 'carries the document reference when it has one')
eq(askContext({ docId: 22, filename: 'v.pdf', reference: 'x', unverified: true }).includes('[unverified reference'),
  true, 'an unverified record is flagged in the context (never stated as fact)')
eq(askContext({ docId: 7, filename: 'd.pdf' }).includes('`pha document 7`'), true,
  'with no page on screen, the document is the context')
eq(askContext({ docId: 7, filename: 'd.pdf' }).includes('page'), false, 'no page mentioned when none is selected')

console.log('mergeDraft — never clobber what the reader already typed')
eq(mergeDraft('', 'CTX'), 'CTX\n\n', 'empty draft: context first, room to type')
eq(mergeDraft('  \n', 'CTX'), 'CTX\n\n', 'whitespace-only draft counts as empty')
eq(mergeDraft('What does this say?', 'CTX'), 'What does this say?\n\nCTX\n\n', 'their question stays, context follows')
eq(mergeDraft(null, 'CTX'), 'CTX\n\n', 'no draft at all')

console.log('parseNumberQuery — "#18" is a document, "1553" is a search')
eq(parseNumberQuery('#18'), 18, 'the form the feedback uses')
eq(parseNumberQuery('  # 7 '), 7, 'whitespace and a space after # are tolerated')
eq(parseNumberQuery('#0'), null, 'document numbers start at 1')
eq(parseNumberQuery('18'), null, 'bare digits stay a full-text search (a year is a query)')
eq(parseNumberQuery('1553'), null, 'a year is not a document number')
eq(parseNumberQuery('#18a'), null, 'only digits after #')
eq(parseNumberQuery('#'), null, 'a bare # is not a number')
eq(parseNumberQuery('#18 extra'), null, 'nothing may follow the number')
eq(parseNumberQuery(''), null, 'empty query')
eq(parseNumberQuery(null), null, 'no query')

console.log('docNumberLabel — the badge every row carries')
eq(docNumberLabel(18), '#18', 'the archive number')
eq(docNumberLabel('18'), '#18', 'accepts the string form the JSON gives')
eq(docNumberLabel(0), '', 'no badge for a missing id')
eq(docNumberLabel(null), '', 'no badge without an id')

console.log('statusUnscanned / statusInbox — shaped from pha status, never re-counted')
const STATUS = {
  new: 3,
  on_hold: 1,
  unscanned: [
    { dir_path: 'collections/tacchi-venturi', count: 1, documents: ['tacchi-venturi.pdf'] },
    { dir_path: '(root)', count: 2, documents: ['a.pdf', 'b.jpg'] },
  ],
  in_inbox: [{ dir_path: 'collections/COLX', count: 1, documents: ['held.pdf'] }],
}
const un = statusUnscanned(STATUS)
eq(un.length, 2, 'one row per unscanned collection')
eq(un[0], { rel_path: 'collections/tacchi-venturi', label: 'tacchi-venturi', count: 1,
  units: [{ rel_path: 'collections/tacchi-venturi/tacchi-venturi.pdf', name: 'tacchi-venturi.pdf', kind: 'pdf' }] },
  'collection label has the collections/ prefix stripped, unit keeps the full relative path')
eq(un[1].label, '(dropbox root)', 'dropbox-root rows are labelled')
eq(un[1].units[0].rel_path, 'a.pdf', 'root units have no leading slash')
eq(un[1].units[1].kind, 'jpg', 'the kind chip comes from the name')
const ib = statusInbox(STATUS)
eq(ib[0].rel_path, 'collections/COLX', 'the inbox row carries the path `pha inbox --move` needs')
eq(ib[0].label, 'COLX', 'inbox label')
eq(statusInbox({ in_inbox: [{ dir_path: '(inbox root)', count: 1, documents: ['x.pdf'] }] })[0].rel_path, '',
  'the inbox root maps to "" (move the whole inbox)')
eq(statusUnscanned(null), [], 'no status -> no rows')
eq(statusInbox({}), [], 'a status without the key -> no rows')

console.log('scanRequestText — the message the view drafts, not a scan it starts')
eq(scanRequestText(STATUS), 'Scan the dropbox items that are not in the archive yet — 3 document(s): tacchi-venturi (1), (dropbox root) (2).',
  'names every collection with its document count')
eq(scanRequestText({ unscanned: [] }), '', 'nothing new -> nothing drafted')

console.log('')
if (failures > 0) {
  console.log(`${failures} check(s) FAILED`)
  process.exit(1)
}
console.log('all view-memory checks passed')
