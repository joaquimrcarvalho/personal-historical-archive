from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import pathlib
import re
import sys

DEFAULT_VAULT = pathlib.Path(
    os.environ.get('OBSIDIAN_VAULT') or (pathlib.Path.home() / 'Obsidian')
).expanduser()
DEFAULT_ARCHIVE = pathlib.Path(
    os.environ.get('PHA_ARCHIVE_DIR') or pathlib.Path.cwd()
).expanduser()
FENCE = '---'


def read_frontmatter(path):
    lines = path.read_text(encoding='utf-8').splitlines()
    if not lines or lines[0].strip() != FENCE:
        return None, lines
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end is None:
        return None, lines
    fm = {}
    for line in lines[1:end]:
        m = re.match(r'^([A-Za-z0-9_\-]+):\s*(.*)$', line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm, lines


def write_frontmatter(path, lines, updates):
    if not lines or lines[0].strip() != FENCE:
        raise SystemExit(str(path) + ': no YAML frontmatter; refusing to stamp')
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end is None:
        raise SystemExit(str(path) + ': unterminated YAML frontmatter')
    out = list(lines)
    for key, value in updates.items():
        pat = re.compile(r'^' + re.escape(key) + r':\s*')
        for i in range(1, end):
            if pat.match(out[i]):
                out[i] = key + ': ' + value
                break
        else:
            out.insert(end, key + ': ' + value)
            end += 1
    path.write_text('\n'.join(out) + '\n', encoding='utf-8')


def sha256_file(path):
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def mtime_iso(path):
    return dt.datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat()


def iter_archive_notes(vault):
    for path in sorted(vault.rglob('*.md')):
        fm, _lines = read_frontmatter(path)
        if fm and fm.get('archive_source'):
            yield path, fm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'stamp'])
    parser.add_argument('--vault', type=pathlib.Path, default=DEFAULT_VAULT,
                        help='Obsidian vault (default: $OBSIDIAN_VAULT or ~/Obsidian)')
    parser.add_argument('--archive', type=pathlib.Path, default=DEFAULT_ARCHIVE,
                        help='pha archive root (default: $PHA_ARCHIVE_DIR or cwd)')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    changed = 0
    checked = 0
    for note, fm in iter_archive_notes(args.vault):
        checked += 1
        src = args.archive / fm['archive_source']
        if not src.exists():
            print('MISSING_SOURCE\t' + str(note) + '\t' + fm['archive_source'])
            changed += 1
            continue
        current_sha = sha256_file(src)
        current_mtime = mtime_iso(src)
        recorded_sha = fm.get('archive_source_sha256', '')
        status = 'UP_TO_DATE' if recorded_sha == current_sha else 'STALE'
        if status == 'STALE':
            changed += 1
        if args.command == 'check':
            print(status + '\t' + str(note) + '\t' + fm['archive_source'])
        else:
            _, lines = read_frontmatter(note)
            updates = {
                'archive_source_sha256': current_sha,
                'archive_source_mtime': current_mtime,
                'vault_synced_at': dt.datetime.now().replace(microsecond=0).isoformat(),
                'vault_note_updated': dt.date.today().isoformat(),
            }
            write_frontmatter(note, lines, updates)
            print('STAMPED\t' + str(note) + '\t' + fm['archive_source'])
        if args.verbose:
            shown = recorded_sha[:12] if recorded_sha else 'missing'
            print('  recorded=' + shown + ' current=' + current_sha[:12] + ' mtime=' + current_mtime)
    if args.command == 'check':
        print('checked=' + str(checked) + ' stale_or_missing=' + str(changed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
