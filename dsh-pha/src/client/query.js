// What the PHA view's search box means — pure, dependency-free.
//
// Archive feedback and notes name documents by NUMBER ("doc #18", "pha page 18 74"), and
// that number is the primary key in every pha command, so the view accepts it directly:
// `#18` addresses a document instead of running a full-text search. Bare digits stay a
// search — a year like `1553` or a page count is a perfectly good query.
//
// scripts/check-view.mjs unit-tests the rule; scripts/build-client.mjs inlines this file
// into lib/client.js.

/** The document number a query addresses, or null when it is an ordinary search.
 *  Accepts `#18`, `# 18` and surrounding whitespace; nothing else. */
export function parseNumberQuery(text) {
  const m = /^#\s*(\d+)$/.exec(String(text == null ? '' : text).trim())
  if (m === null) return null
  const n = Number(m[1])
  return Number.isSafeInteger(n) && n > 0 ? n : null
}

/** The badge a document row shows — the number every pha command and every piece of
 *  feedback uses to name it. */
export function docNumberLabel(id) {
  const n = Number(id)
  return Number.isSafeInteger(n) && n > 0 ? '#' + n : ''
}
