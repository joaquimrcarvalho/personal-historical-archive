// The view's sections, derived from `pha status --json` — pure, dependency-free.
//
// `pha status` is the archive's own answer to "what is in the dropbox but not in the
// archive yet" and "what is parked in the inbox": it applies the CLI's rules (a
// directory-of-images counts as ONE document, sidecars are not documents, the inbox is
// excluded from the dropbox walk). The view renders those two lists and must not invent
// a second definition of them, so this module only *shapes* the status payload — no
// filesystem walking, no counting of its own.
//
// scripts/check-view.mjs unit-tests it; scripts/build-client.mjs inlines it.

function shortLabel(dirPath, rootLabel) {
  const dir = String(dirPath == null ? '' : dirPath)
  if (dir === '' || dir === '(root)' || dir === '(inbox root)') return rootLabel
  return dir.replace(/^collections\//, '')
}

function kindOf(name) {
  const m = /\.([A-Za-z0-9]+)$/.exec(String(name || ''))
  return m ? m[1].toLowerCase() : 'file'
}

/** Shape one status group list (`unscanned` / `in_inbox`) for the view: a collection
 *  row with its documents, and the relative path the CLI would act on
 *  (`pha inbox --move <rel_path>`; '' means the whole thing). */
function groups(rows, rootLabel) {
  return (rows || []).map((g) => {
    const dir = String((g && g.dir_path) || '')
    const root = dir === '' || dir === '(root)' || dir === '(inbox root)'
    const base = root ? '' : dir
    const names = (g && g.documents) || []
    return {
      rel_path: base,
      label: shortLabel(dir, rootLabel),
      count: typeof g.count === 'number' ? g.count : names.length,
      units: names.map((name) => ({
        rel_path: base ? base + '/' + name : name,
        name: name,
        kind: kindOf(name),
      })),
    }
  })
}

/** Dropbox items with no database row — "new", never scanned. */
export function statusUnscanned(status) {
  return groups(status && status.unscanned, '(dropbox root)')
}

/** Documents parked on hold in the inbox. */
export function statusInbox(status) {
  return groups(status && status.in_inbox, '(inbox root)')
}

/** A message asking for the new items to be scanned — drafted into the composer (the
 *  view never starts a scan itself: it takes a model-server lock). */
export function scanRequestText(status) {
  const items = statusUnscanned(status)
  const total = items.reduce((n, g) => n + g.count, 0)
  if (total === 0) return ''
  const list = items.map((g) => g.label + ' (' + g.count + ')').join(', ')
  return 'Scan the dropbox items that are not in the archive yet — ' + total
    + ' document(s): ' + list + '.'
}
