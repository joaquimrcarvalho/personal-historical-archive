#!/usr/bin/env node
// Unit checks for the notes viewer's link rules.
//
//   node dsh-pha/scripts/check-links.mjs
//
// src/client/links.js is deliberately dependency-free so this file can import it
// directly (no React, no DOM); scripts/build-client.mjs inlines the same source into
// the served bundle. The slug rules are checked against pha's own
// `addresses.doc_slug`, whose docstring promises two implementations agree — so a
// citation link resolves to the right document in the viewer.
import {
  classifyLink,
  docSlugFor,
  foldNoteKey,
  parseWikilink,
  resolveNoteName,
  slugifyPath,
} from '../src/client/links.js'

let failures = 0
const eq = (actual, expected, label) => {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a === e) console.log('  ok   ' + label)
  else { failures++; console.log('  FAIL ' + label + '\n         got      ' + a + '\n         expected ' + e) }
}

console.log('slugifyPath — mirrors pha addresses.doc_slug')
eq(slugifyPath('collections/documenta-indica/DOCUMENTA-INDICA-1550-1553.pdf'), 'documenta-indica-documenta-indica-1550-1553', 'collections/ dropped, extension dropped, lowered')
eq(slugifyPath('collections/COLX/d.pdf'), 'colx-d', 'short path')
eq(slugifyPath('Missões dos jesuítas.pdf'), 'missoes-dos-jesuitas', 'accents folded')
eq(slugifyPath('a/b c/d_e-f.pdf'), 'a-b-c-d-e-f', 'non-alphanumerics collapse to one -')
eq(slugifyPath('../../x/y.pdf'), 'x-y', 'leading .. segments do not produce a leading -')
eq(slugifyPath(''), '', 'empty stays empty')
eq(slugifyPath('collections'), '', 'only the collections/ prefix -> empty')

console.log('docSlugFor — /pha/documents rows')
eq(docSlugFor({ dir_path: 'collections/documenta-indica', filename: 'DOCUMENTA-INDICA-1550-1553.pdf' }),
  'documenta-indica-documenta-indica-1550-1553', 'dir_path + filename')
eq(docSlugFor({ dir_path: '', filename: '1576.pdf' }), '1576', 'root document')
eq(docSlugFor(null), '', 'no document')

console.log('parseWikilink')
eq(parseWikilink('Malaca'), { target: 'Malaca', anchor: null, label: 'Malaca' }, 'plain')
eq(parseWikilink('Malaca|the strait'), { target: 'Malaca', anchor: null, label: 'the strait' }, 'alias')
eq(parseWikilink('Malaca#Trade'), { target: 'Malaca', anchor: 'Trade', label: 'Malaca' }, 'heading anchor')
eq(parseWikilink('malaca-note.md'), { target: 'malaca-note', anchor: null, label: 'malaca-note' }, '.md suffix dropped')
eq(parseWikilink(' The Strait | alias # not-an-anchor '), { target: 'The Strait', anchor: null, label: 'alias # not-an-anchor' }, 'alias wins over a later #')

console.log('foldNoteKey / resolveNoteName')
eq(foldNoteKey('The Strait'), 'the-strait', 'spaces -> -')
eq(foldNoteKey('The_Strait.md'), 'the-strait', 'underscores + .md')
eq(resolveNoteName('Malaca', ['Malaca']), 'Malaca', 'exact')
eq(resolveNoteName('malaca', ['Malaca']), 'Malaca', 'case-insensitive')
eq(resolveNoteName('the strait', ['The-Strait']), 'The-Strait', 'spacing-insensitive')
eq(resolveNoteName('missões', ['Missoes']), 'Missoes', 'accents-insensitive')
eq(resolveNoteName('malaca-trade', ['Malaca Trade']), 'Malaca Trade', 'slug-equal fallback')
eq(resolveNoteName('missing', ['Malaca']), null, 'unresolved -> null')

console.log('classifyLink')
eq(classifyLink('#fn-3'), { kind: 'anchor', id: 'fn-3' }, 'same-page anchor')
eq(classifyLink('http://127.0.0.1:8765/doc/foo-bar/p437'),
  { kind: 'citation', slug: 'foo-bar', page: 437, image: false, href: 'http://127.0.0.1:8765/doc/foo-bar/p437' }, 'phaserve citation')
eq(classifyLink('/doc/colx-d/p12'),
  { kind: 'citation', slug: 'colx-d', page: 12, image: false, href: '/doc/colx-d/p12' }, 'relative citation')
eq(classifyLink('http://127.0.0.1:8765/doc/foo-bar/'),
  { kind: 'citation', slug: 'foo-bar', page: null, image: false, href: 'http://127.0.0.1:8765/doc/foo-bar/' }, 'overview URL')
eq(classifyLink('http://127.0.0.1:8765/doc/foo-bar/p437.jpg'),
  { kind: 'citation', slug: 'foo-bar', page: 437, image: true, href: 'http://127.0.0.1:8765/doc/foo-bar/p437.jpg' }, 'page image')
eq(classifyLink('other-note.md'), { kind: 'note', name: 'other-note', href: 'other-note.md' }, 'relative note')
eq(classifyLink('../notes/other-note.md'), { kind: 'note', name: 'other-note', href: '../notes/other-note.md' }, 'nested relative note')
eq(classifyLink('https://example.com/x'), { kind: 'external', href: 'https://example.com/x' }, 'https')
eq(classifyLink('mailto:a@b.c'), { kind: 'external', href: 'mailto:a@b.c' }, 'mailto')
eq(classifyLink('assets/img.png'), { kind: 'external', href: 'assets/img.png' }, 'unknown relative -> external')
eq(classifyLink(''), { kind: 'external', href: '' }, 'empty')

console.log('')
if (failures > 0) {
  console.log(`${failures} check(s) FAILED`)
  process.exit(1)
}
console.log('all link checks passed')
