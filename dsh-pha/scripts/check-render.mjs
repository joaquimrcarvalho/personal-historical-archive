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
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
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

// Mirror scripts/build-client.mjs: the link helpers are a separate dependency-free
// module that the served bundle inlines, so the check inlines it too.
const links = readFileSync(join(dirname(sourcePath), 'links.js'), 'utf8').replace(/^export\s+/gm, '')
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
vm.runInContext(`${runtime}\n${links}\n${src}\n;globalThis.__pha = { inline, renderMd, renderNote, classifyLink, parseWikilink, resolveNoteName };`,
  sandbox, { filename: sourcePath })

const { renderNote, renderMd } = sandbox.__pha

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
const click = (n) => { if (n.props.onClick) n.props.onClick({ preventDefault() {} }); }

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
const citation = byClass('pha-page-link')[0]
if (citation) { citation.props.onClick({ preventDefault() {} }) }
const anchorLink = anchors.find((n) => n.props.href === '#Heading-one')
if (anchorLink) { anchorLink.props.onClick({ preventDefault() {} }) }

check(JSON.stringify(clicks[0]) === JSON.stringify(['note', 'Malaca', null]),
  'clicking an alias wikilink asks for the note, not the alias')
check(JSON.stringify(clicks[1]) === JSON.stringify(['note', 'missing-note', null]),
  'clicking a dangling link still tries the note (the pane then explains why)')
check(JSON.stringify(clicks[2]) === JSON.stringify(['cite', 'colx-d', 437]),
  'clicking a citation asks for slug + page')
check(JSON.stringify(clicks[3]) === JSON.stringify(['anchor', 'Heading-one']),
  'clicking an anchor scrolls instead of navigating')

check(byClass('pha-fn-back').length === 1, 'each footnote links back to its reference')

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

console.log('')
if (failures > 0) {
  console.log(`${failures} check(s) FAILED`)
  process.exit(1)
}
console.log('all render checks passed')
