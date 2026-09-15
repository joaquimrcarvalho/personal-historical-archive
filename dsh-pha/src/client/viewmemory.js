// Reading-position memory for the PHA view — pure, dependency-free, DOM-free.
//
// The conversation view is unmounted when the reader switches to the Chat tab, and
// React state dies with the component, so the document and page they were reading were
// forgotten. The client keeps a snapshot per session in module memory (page lifetime,
// fresh on reload) and this module owns the two halves of that: what to remember, and
// what to load again when the view comes back. The data itself is never cached here —
// only the selection, so a restored view always reads current text.
//
// scripts/check-view.mjs unit-tests these rules; scripts/build-client.mjs inlines the
// file into lib/client.js (the served bundle stays one dependency-free module).

/** What to remember: the selection (document + page + variant, note, definition, inbox
 *  item, search results) and the view preferences. */
export function snapshotView(state, ui) {
  const s = state || {}
  const u = ui || {}
  const doc = (s.selectedId === null || s.selectedId === undefined)
    ? null
    : {
        id: s.selectedId,
        page: s.pageReq ? s.pageReq.page : null,
        edited: !!(s.pageReq && s.pageReq.edited),
      }
  return {
    doc: doc,
    note: s.selectedNote ? s.selectedNote.name : null,
    def: s.selectedDef ? { path: s.selectedDef.path } : null,
    inbox: s.inboxSel || null,
    search: (s.searchMode && s.hits) ? { query: s.query || '', hits: s.hits, pageVariant: s.searchPageVariant || null } : null,
    plain: s.plainOverride === undefined ? null : s.plainOverride,
    ui: {
      textOn: u.textOn !== false,
      showImg: !!u.showImg,
      leftPct: Number(u.leftPct) > 0 ? Number(u.leftPct) : 38,
    },
  }
}

/** Seed a fresh component state from a snapshot: the *identity* of the selection comes
 *  back synchronously (so nothing flashes empty), while its content is re-fetched by
 *  `restorePlan`. A document opened from search results keeps both. */
export function seedState(defaults, memory) {
  const s = Object.assign({}, defaults)
  const m = memory || {}
  if (m.search) {
    s.searchMode = true
    s.hits = m.search.hits || null
    s.query = m.search.query || ''
    s.searchPageVariant = m.search.pageVariant || null
  }
  if (m.doc) {
    s.selectedId = m.doc.id
    s.pageReq = m.doc.page ? { doc: m.doc.id, page: m.doc.page, edited: !!m.doc.edited } : null
  }
  if (m.plain === true || m.plain === false) s.plainOverride = m.plain
  if (m.note) s.noteMode = true
  if (m.def) s.defMode = true
  if (m.inbox) s.inboxSel = m.inbox
  return s
}

/** Seed the view toggles (they are separate React states). */
export function seedUi(memory) {
  const u = (memory && memory.ui) || {}
  return {
    textOn: u.textOn !== false,
    showImg: !!u.showImg,
    leftPct: Number(u.leftPct) > 0 ? Number(u.leftPct) : 38,
  }
}

/** What still has to be LOADED for a seeded state; null when nothing does (a restored
 *  search already carries its hits). Only one of these can be the current reading
 *  position, so a document wins over a note or a definition. */
export function restorePlan(memory) {
  const m = memory || {}
  if (m.doc) return { kind: 'document', id: m.doc.id, page: m.doc.page, edited: !!m.doc.edited }
  if (m.note) return { kind: 'note', name: m.note }
  if (m.def) return { kind: 'definition', path: m.def.path }
  if (m.inbox) return { kind: 'inbox', sel: m.inbox }
  return null
}
