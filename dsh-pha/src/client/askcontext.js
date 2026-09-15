// "Ask about this page" — the context a question carries, as pure text.
//
// The PHA view is a conversation target, so the harness hands it `inputActions` (the
// input shell's actions — the shell itself calls `inputActions.setDraft(draft)`) and
// `useInput` (the input state, with `.draft`). That is enough to put an accurate
// citation of what is on screen into the composer, without any host-side change.
//
// The text is deliberately a *pointer*, not a transcript: the agent has the `pha_page`
// tool (and the citation discipline in AGENTS.md says to recover the full page before
// answering), so naming the exact page and variant is better than pasting text that can
// go stale the moment a historian corrects the page.
//
// scripts/check-view.mjs unit-tests these rules; scripts/build-client.mjs inlines the
// file into lib/client.js.

/** A compact, citation-accurate context header for a question about one page (or one
 *  document when no page is on screen). */
export function askContext(info) {
  const i = info || {}
  const hasDoc = i.docId !== null && i.docId !== undefined && i.docId !== ''
  const who = i.filename
    ? String(i.filename) + (hasDoc ? ' (doc ' + i.docId + ')' : '')
    : (hasDoc ? 'doc ' + i.docId : 'the archive')
  const variant = i.variant
    ? ' — ' + i.variant + (i.editor ? ' (' + i.editor + ')' : '') + ' variant'
    : ''
  const lines = ['Context: ' + who + (i.page ? ', page ' + i.page : '') + variant]
  if (i.reference) {
    lines.push('Reference: ' + String(i.reference).trim()
      + (i.unverified ? ' [unverified reference — confirm it before citing]' : ''))
  }
  if (i.page && hasDoc) {
    lines.push('Read the full page with `pha page ' + i.docId + ' ' + i.page
      + (i.variant === 'edited' ? ' --edited' : '') + '` (or the pha_page tool).')
  } else if (hasDoc) {
    lines.push('Read the document with `pha document ' + i.docId + '` (or the pha_document tool).')
  }
  return lines.join('\n')
}

/** Draft text: the context above an empty draft, or appended below what the reader has
 *  already typed (never clobbering it), with room to keep typing either way. */
export function mergeDraft(existing, context) {
  const e = String(existing == null ? '' : existing)
  if (e.trim() === '') return context + '\n\n'
  return e.replace(/\s+$/, '') + '\n\n' + context + '\n\n'
}
