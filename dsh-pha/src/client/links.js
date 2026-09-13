// Pure link logic for the PHA notes viewer — no imports, no DOM.
//
// Two consumers keep this file honest:
//   * src/client/index.js imports it (scripts/build-client.mjs inlines it into
//     lib/client.js, so the served bundle stays a single dependency-free module);
//   * scripts/check-links.mjs imports it directly and unit-tests every rule.
//
// The rules it encodes are the ones the archive's own documentation promises:
// `[[wikilinks]]`, `[^n]` footnote references, note-relative `.md` links, and the
// `pha cite` viewer URLs (`/doc/<slug>/p<NNN>`) that notes carry as citations.

/** pha's `addresses.doc_slug` rule, reimplemented for the browser.
 *
 *  Mechanical by contract (the Python docstring says so explicitly): drop a
 *  leading `collections/`, drop the file extension, lowercase, fold accents to
 *  ASCII, collapse every run of non-alphanumerics to `-`, trim `-`. No date and
 *  no hash, so a slug survives re-processing. */
export function slugifyPath(relPath) {
  const parts = String(relPath == null ? '' : relPath)
    .split('/')
    .filter((p) => p !== '' && p !== '.')
  if (parts.length > 0 && parts[0] === 'collections') parts.shift()
  if (parts.length === 0) return ''
  const last = parts[parts.length - 1]
  const dot = last.lastIndexOf('.')
  if (dot > 0) parts[parts.length - 1] = last.slice(0, dot)
  const text = parts.join('/').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
  return text.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

/** Slug of one `/pha/documents` row: `dir_path` + `filename`, dropbox-relative. */
export function docSlugFor(doc) {
  if (!doc) return ''
  const dir = String(doc.dir_path || '')
  const file = String(doc.filename || '')
  return slugifyPath(dir ? dir + '/' + file : file)
}

/** `[[target#anchor|label]]` -> `{ target, anchor, label }`.
 *
 *  Handles Obsidian's alias (`|`), heading anchor (`#`) and an explicit `.md`
 *  suffix; the viewer addresses notes by name, so the suffix is dropped. */
export function parseWikilink(raw) {
  let body = String(raw == null ? '' : raw).trim()
  let label = null
  const bar = body.indexOf('|')
  if (bar >= 0) {
    label = body.slice(bar + 1).trim()
    body = body.slice(0, bar).trim()
  }
  let anchor = null
  const hash = body.indexOf('#')
  if (hash >= 0) {
    anchor = body.slice(hash + 1).trim() || null
    body = body.slice(0, hash).trim()
  }
  body = body.replace(/\.md$/i, '')
  const text = label && label.length > 0 ? label : body
  return { target: body, anchor, label: text || String(raw == null ? '' : raw) }
}

/** Matching key for a note name or wikilink target: case-insensitive, and
 *  spaces/underscores/hyphens all become one `-`, so `[[the strait]]`,
 *  `[[The_Strait]]` and `the-strait` resolve to the same note. */
export function foldNoteKey(name) {
  return String(name == null ? '' : name)
    .trim()
    .replace(/\.md$/i, '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[\s_]+/g, '-')
    .replace(/-+/g, '-')
}

/** Resolve a wikilink target against the known note names (null = unresolved).
 *
 *  Three passes: exact, folded (case/spacing), then slug-equal — which is what
 *  makes `[[malaca trade]]` find `malaca-trade.md`. */
export function resolveNoteName(target, names) {
  const key = foldNoteKey(target)
  if (key === '') return null
  const list = names || []
  for (const n of list) if (String(n) === String(target)) return n
  for (const n of list) if (foldNoteKey(n) === key) return n
  for (const n of list) if (slugifyPath(String(n)) === key) return n
  return null
}

/** Classify a markdown link destination.
 *
 *  - `anchor`   → `#id`, scroll inside the current note
 *  - `citation` → `/doc/<slug>/p<NNN>` (or the `/doc/<slug>/` overview): the page
 *                 can be opened INSIDE this view instead of leaving the harness
 *  - `note`     → a relative `….md` link: open that note in the notes viewer
 *  - `external` → anything else (http(s), mailto:, unknown paths): new tab, so
 *                 the harness page is never navigated away from
 */
export function classifyLink(url) {
  const raw = String(url == null ? '' : url).trim()
  if (raw === '') return { kind: 'external', href: raw }
  if (raw.charAt(0) === '#') return { kind: 'anchor', id: raw.slice(1) }
  const cite = /(?:^|\/\/[^/]+)?\/doc\/(.+?)(?:\/p(\d+)(\.jpe?g)?)?\/?$/i.exec(raw)
  if (cite && cite[1]) {
    return {
      kind: 'citation',
      slug: cite[1].replace(/\/+$/, ''),
      page: cite[2] === undefined ? null : Number(cite[2]),
      image: cite[3] !== undefined,
      href: raw,
    }
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith('//')) return { kind: 'external', href: raw }
  const md = /^(?:.*\/)?([^/]+)\.md$/i.exec(raw)
  if (md) return { kind: 'note', name: md[1], href: raw }
  return { kind: 'external', href: raw }
}
