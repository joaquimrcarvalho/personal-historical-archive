#!/usr/bin/env python3
"""Check or stamp the provenance frontmatter of archive-derived vault notes.

The vault is addressed through Obsidian's **Local REST API** — the same API the
Obsidian MCP server uses — so there is no vault path anywhere: the vault is
whatever Obsidian has open. Candidates come from `POST /search/simple/` (so
Obsidian's own index decides what exists), reads from `GET /vault/<path>` and
the stamp writes through `PUT /vault/<path>`. Nothing is written behind
Obsidian's back.

    python3 archive_note_sync.py check    --archive /path/to/archive
    python3 archive_note_sync.py stamp    --archive /path/to/archive
    python3 archive_note_sync.py identify --vault jrc-notes --archive /path/to/archive

The API cannot say WHICH vault it serves, so each vault carries an identity: a
`.pha-vault.md` note at its root naming the vault and the archive it belongs to.
Every run starts by printing it. `stamp` refuses to write to a vault that has no
sentinel, and both commands refuse a vault whose sentinel names a different
archive — the wrong vault is otherwise indistinguishable from an empty one.

Environment:
    OBSIDIAN_API_KEY    the Local REST API key (required; the MCP server's key)
    OBSIDIAN_BASE_URL   default http://127.0.0.1:27123
    PHA_ARCHIVE_DIR     default for --archive (else the current directory)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

FENCE = '---'
SEARCH_TERM = 'archive_source'
SENTINEL = '.pha-vault.md'
DEFAULT_BASE_URL = os.environ.get('OBSIDIAN_BASE_URL') or 'http://127.0.0.1:27123'
DEFAULT_ARCHIVE = pathlib.Path(
    os.environ.get('PHA_ARCHIVE_DIR') or pathlib.Path.cwd()
).expanduser()


class Vault:
    """Obsidian's Local REST API, addressed by vault-relative path."""

    def __init__(self, base_url=DEFAULT_BASE_URL, api_key=None, timeout=20):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key or os.environ.get('OBSIDIAN_API_KEY')
        self.timeout = timeout
        if not self.api_key:
            raise SystemExit(
                'OBSIDIAN_API_KEY is not set: the script talks to Obsidian over'
                ' its Local REST API (the key the MCP server uses).')

    def _call(self, method, path, query=None, body=None, content_type=None,
              missing_ok=False):
        url = self.base_url + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        data = body.encode('utf-8') if isinstance(body, str) else body
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header('Authorization', 'Bearer ' + self.api_key)
        if content_type:
            request.add_header('Content-Type', content_type)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            if missing_ok and err.code == 404:
                return None
            if err.code in (401, 403):
                raise SystemExit(
                    'Obsidian refused the API key (HTTP %d): check '
                    'OBSIDIAN_API_KEY.' % err.code)
            raise SystemExit(
                'Obsidian returned HTTP %d for %s %s' % (err.code, method, path))
        except urllib.error.URLError as err:
            raise SystemExit(
                'cannot reach the Obsidian Local REST API at %s (%s). Is Obsidian'
                ' running with the Local REST API plugin enabled?'
                % (self.base_url, err.reason))

    def paths(self):
        raw = self._call('GET', '/vault/')
        return list(json.loads(raw or b'{}').get('files') or [])

    def shape(self, limit=6):
        """(first few top-level entries, total entries) — a recognisable face."""
        entries = self.paths()
        names = [e for e in entries if '/' not in e.rstrip('/')]
        return names[:limit], len(entries)

    def candidates(self):
        raw = self._call('POST', '/search/simple/',
                         query={'query': SEARCH_TERM, 'contextLength': 0})
        hits = json.loads(raw or b'[]')
        return sorted({h['filename'] for h in hits if h.get('filename')})

    def read(self, path):
        return self._call(
            'GET', '/vault/' + urllib.parse.quote(path, safe='/')).decode('utf-8')

    def read_or_none(self, path):
        raw = self._call('GET', '/vault/' + urllib.parse.quote(path, safe='/'),
                         missing_ok=True)
        return None if raw is None else raw.decode('utf-8')

    def write(self, path, text):
        self._call('PUT', '/vault/' + urllib.parse.quote(path, safe='/'),
                   body=text, content_type='text/markdown')


def read_frontmatter(text, name='<note>'):
    """Return (frontmatter dict or None, lines) for one note's text."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return None, lines
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end is None:
        return None, lines
    frontmatter = {}
    for line in lines[1:end]:
        match = re.match(r'^([A-Za-z0-9_\-]+):\s*(.*)$', line)
        if match:
            frontmatter[match.group(1)] = match.group(2).strip()
    return frontmatter, lines


def apply_updates(lines, updates, name='<note>'):
    """Return the note with the given frontmatter keys set (body untouched)."""
    if not lines or lines[0].strip() != FENCE:
        raise SystemExit(name + ': no YAML frontmatter; refusing to stamp')
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end is None:
        raise SystemExit(name + ': unterminated YAML frontmatter')
    out = list(lines)
    for key, value in updates.items():
        pattern = re.compile(r'^' + re.escape(key) + r':\s*')
        for i in range(1, end):
            if pattern.match(out[i]):
                out[i] = key + ': ' + value
                break
        else:
            out.insert(end, key + ': ' + value)
            end += 1
    return '\n'.join(out) + '\n'


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def mtime_iso(path):
    return dt.datetime.fromtimestamp(path.stat().st_mtime).replace(
        microsecond=0).isoformat()


def sentinel_text(vault_name, archive, role='notes'):
    return (
        '---\n'
        'vault: ' + vault_name + '\n'
        'pha_archive: ' + str(archive) + '\n'
        'role: ' + role + '\n'
        '---\n\n'
        'Written by pha (`archive_note_sync.py identify`) so an agent can tell\n'
        'which vault it is talking to. The Obsidian API cannot name its vault,\n'
        'and the wrong vault looks exactly like an empty one. Do not edit by\n'
        'hand; re-run `identify` instead.\n'
    )


def resolved(path):
    return pathlib.Path(path).expanduser().resolve()


def identify(vault, archive):
    """Say which vault the API serves: (state, line).

    state is 'ok' (sentinel matches this archive), 'missing' (no sentinel:
    identity unknown), or 'wrong' (the sentinel belongs to another archive).
    """
    names, total = vault.shape()
    face = ', '.join(names) if names else '(no top-level entries)'
    raw = vault.read_or_none(SENTINEL)
    if raw is None:
        return 'missing', ('vault: UNIDENTIFIED \u2014 %d entries \u2014 top-level: %s'
                           % (total, face))
    frontmatter, _ = read_frontmatter(raw, SENTINEL)
    frontmatter = frontmatter or {}
    name = frontmatter.get('vault') or '(unnamed)'
    recorded = frontmatter.get('pha_archive') or ''
    same = bool(recorded) and resolved(recorded) == resolved(archive)
    if not same:
        return 'wrong', ('vault: WRONG \u2014 %s says archive %s, this run is for %s'
                         % (name, recorded or '(none)', resolved(archive)))
    return 'ok', ('vault: %s \u2014 %d entries \u2014 archive: %s'
                  % (name, total, recorded))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Check, stamp or identify archive provenance in Obsidian '
                    'notes (through the Local REST API; no vault path).')
    parser.add_argument('command', choices=['check', 'stamp', 'identify'])
    parser.add_argument('--archive', type=pathlib.Path, default=DEFAULT_ARCHIVE,
                        help='pha archive root (default: $PHA_ARCHIVE_DIR or cwd)')
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL,
                        help='Obsidian Local REST API (default: $OBSIDIAN_BASE_URL'
                             ' or http://127.0.0.1:27123)')
    parser.add_argument('--key', default=None,
                        help='API key (default: $OBSIDIAN_API_KEY)')
    parser.add_argument('--vault', default=None,
                        help='identify only: the vault name to record '
                             '(folder basename, e.g. jrc-notes)')
    parser.add_argument('--role', default='notes',
                        help='identify only: a short role, e.g. notes or main-notes')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)

    vault = Vault(base_url=args.base_url, api_key=args.key)

    if args.command == 'identify':
        if not args.vault:
            raise SystemExit(
                'identify needs --vault <name> (the folder basename the Obsidian'
                ' CLI accepts, e.g. --vault jrc-notes).')
        state, line = identify(vault, args.archive)
        print(line)
        if state == 'wrong':
            raise SystemExit(
                'refusing to overwrite a sentinel that names another archive:'
                ' that is evidence, not a gap to fill. Decide which side is wrong')
        vault.write(SENTINEL, sentinel_text(args.vault, str(resolved(args.archive)),
                                            args.role))
        print('wrote ' + SENTINEL + ' \u2014 vault: ' + args.vault
              + ' \u2014 archive: ' + str(resolved(args.archive)))
        return 0

    state, line = identify(vault, args.archive)
    print(line)
    if state == 'wrong':
        raise SystemExit(
            'refusing to work on the wrong vault: the sentinel names another'
            ' archive. Nothing was read or written.')
    if state == 'missing':
        if args.command == 'stamp':
            raise SystemExit(
                'vault is UNIDENTIFIED: this vault carries no ' + SENTINEL + ', so'
                ' it cannot be proven to be the intended one. Confirm with the user'
                ' that the vault printed above is the right one, then run'
                ' `archive_note_sync.py identify --vault <name> --archive '
                + str(resolved(args.archive)) + '` and retry. Nothing was written.')
        print('warning: vault is UNIDENTIFIED (no ' + SENTINEL + '); reads only')

    checked = 0
    changed = 0
    for note in vault.candidates():
        if not note.endswith('.md'):
            continue
        text = vault.read(note)
        frontmatter, lines = read_frontmatter(text, note)
        if not frontmatter or not frontmatter.get('archive_source'):
            continue
        checked += 1
        source = args.archive / frontmatter['archive_source']
        if not source.exists():
            print('MISSING_SOURCE\t' + note + '\t' + frontmatter['archive_source'])
            changed += 1
            continue
        current_sha = sha256_file(source)
        current_mtime = mtime_iso(source)
        recorded_sha = frontmatter.get('archive_source_sha256', '')
        status = 'UP_TO_DATE' if recorded_sha == current_sha else 'STALE'
        if status == 'STALE':
            changed += 1
        if args.command == 'check':
            print(status + '\t' + note + '\t' + frontmatter['archive_source'])
        else:
            updates = {
                'archive_source_sha256': current_sha,
                'archive_source_mtime': current_mtime,
                'vault_synced_at': dt.datetime.now().replace(
                    microsecond=0).isoformat(),
                'vault_note_updated': dt.date.today().isoformat(),
            }
            vault.write(note, apply_updates(lines, updates, note))
            print('STAMPED\t' + note + '\t' + frontmatter['archive_source'])
        if args.verbose:
            shown = recorded_sha[:12] if recorded_sha else 'missing'
            print('  recorded=' + shown + ' current=' + current_sha[:12]
                  + ' mtime=' + current_mtime)
    if args.command == 'check':
        print('checked=' + str(checked) + ' stale_or_missing=' + str(changed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
