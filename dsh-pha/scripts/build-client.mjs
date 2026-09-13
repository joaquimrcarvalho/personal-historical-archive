#!/usr/bin/env node
// Build dsh-pha's browser client module: compile src/client/index.js into
// lib/client.js in the harness client-module bundle format.
//
// The harness's dsh-client-modules head half reads exports["./client"] (package.json)
// -> lib/client.js at runtime and serves it to the browser. The browser module
// system expects a bundle that calls window.__ModuleLoader__.load({ id, factory });
// the factory is a CJS closure (module/exports/require) that keeps "react" external
// and returns module.exports with a NAMED `apply` export (the cordis plugin face).
//
// Use after editing src/client/index.js:
//   node scripts/build-client.mjs
// then commit lib/client.js so the package ships self-contained.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const srcPath = join(root, 'src/client/index.js')
const outDir = join(root, 'lib')
const outPath = join(outDir, 'client.js')
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const id = pkg.name

let body = readFileSync(srcPath, 'utf8')

// We provide React via require("react") + the __toESM interop in the factory,
// so drop the ESM import (single-line react import) from the bundle body.
body = body.replace(/^import[^\n]*from\s*['"]react['"];?\s*\n/m, '')
// The link helpers live in their own dependency-free module (src/client/links.js) so
// scripts/check-links.mjs can unit-test them directly. The served bundle must stay ONE
// self-contained module, so inline that file here.
const linksPath = join(root, 'src/client/links.js')
let links = readFileSync(linksPath, 'utf8')
if (/^\s*import\s/m.test(links)) {
  throw new Error('build-client: src/client/links.js must not import anything — it is inlined into the bundle')
}
links = links.replace(/^export\s+/gm, '').replace(/\s+$/, '\n')
const linksImport = body.match(/^import[^\n]*from\s*['"]\.\/links\.js['"];?\s*\n/m)
if (linksImport === null) {
  throw new Error("build-client: src/client/index.js must import the link helpers from './links.js'")
}
body = body.replace(linksImport[0], links)

// Keep any other top-level imports out of the bundle (there are none today, but
// if one appears we want a loud failure rather than a silently broken bundle).
const leftoverImport = body.match(/^import\s/m)
if (leftoverImport) {
  throw new Error(`build-client: unexpected remaining ESM import — bundle it instead of inlining:\n${body.slice(leftoverImport.index, leftoverImport.index + 80)}`)
}
// Named-export apply -> plain const so the factory closes over it.
body = body.replace(/^export\s+const\s+apply\s*=/m, 'const apply = ')
body = body.replace(/^export\s+function\s+apply\b/m, 'function apply ')
// Named-export inject -> plain const (cordis reads the plugin's static inject
// to know which services must exist before apply runs).
body = body.replace(/^export\s+const\s+inject\s*=/m, 'const inject = ')
// A stray default export (old style) would not be the named face the loader reads.
body = body.replace(/^export\s+default\b/m, '/* build-client: default export dropped (named apply expected) */')
body = body.replace(/\s+$/, '\n')

// Mirrors the rolldown runtime shim the harness bundles emit, so React has the
// same shape (namespace methods + `.default`) the core client modules rely on.
const runtime = [
  'let __create = Object.create;',
  'let __defProp = Object.defineProperty;',
  'let __getOwnPropDesc = Object.getOwnPropertyDescriptor;',
  'let __getOwnPropNames = Object.getOwnPropertyNames;',
  'let __getProtoOf = Object.getPrototypeOf;',
  'let __hasOwnProp = Object.prototype.hasOwnProperty;',
  'let __copyProps = (to, from, except, desc) => {',
  '\tif (from && (typeof from === "object" || typeof from === "function"))',
  '\t\tfor (let keys = __getOwnPropNames(from), i = 0, n = keys.length, key; i < n; i++) {',
  '\t\t\tkey = keys[i];',
  '\t\t\tif (!__hasOwnProp.call(to, key) && key !== except)',
  '\t\t\t\t__defProp(to, key, { get: ((k) => from[k]).bind(null, key), enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });',
  '\t\t}',
  '\treturn to;',
  '};',
  'let __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target, mod));',
  '',
  'let React = require("react");',
  'React = __toESM(React, 1);',
  '',
].join('\n')

const out = [
  `window.__ModuleLoader__.load({`,
  `\tid: ${JSON.stringify(id)},`,
  `\tfactory: (require) => {`,
  `\t\tvar module = { exports: {} };`,
  `\t\tvar exports = module.exports;`,
  `\t\tObject.defineProperty(exports, Symbol.toStringTag, { value: "Module" });`,
  runtime,
  body,
  `\t\texports.apply = apply;`,
  `\t\tif (typeof inject !== "undefined") exports.inject = inject;`,
  `\t\treturn module.exports;`,
  `\t}`,
  `});`,
].join('\n')

mkdirSync(outDir, { recursive: true })
writeFileSync(outPath, out)
console.log(`built ${outPath} (${Buffer.byteLength(out)} bytes)`)
