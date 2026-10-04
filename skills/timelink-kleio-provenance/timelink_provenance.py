#!/usr/bin/env python3
"""Timelink/Kleio provenance helper.

Set MHK_HOME (or MHK) to the Timelink home, or pass a .sqlite path as --db.

Usage:
  python3 timelink_provenance.py --db dehergne --entity deh-antoine-thomas
  python3 timelink_provenance.py --db china_coimbra --entity duarte-de-sande --value Coimbra
  python3 timelink_provenance.py --db /path/to/dehergne.sqlite --entity deh-adriano-pestana --json

For each matching attribute it prints the attr_id, the Kleio file and line
(from entities.the_line / sources.kleiofile), and a vscode://file/<abs>:<line>
link. Read-only: it never writes to the database or the Kleio files.
"""
import argparse, glob, json, os, sqlite3, sys

MHK = os.path.expanduser(
    os.environ.get('MHK_HOME') or os.environ.get('MHK') or '~/mhk-home'
)

def resolve_db(name):
    if os.path.isfile(name):
        return os.path.abspath(name)
    pats = [os.path.join(MHK, 'sources', '*', 'database', 'sqlite', name + '.sqlite'),
            os.path.join(MHK, 'sources', '*', 'database', 'sqlite', name),
            os.path.join(MHK, '**', name + '.sqlite')]
    for p in pats:
        hits = sorted(glob.glob(p, recursive=True))
        if hits:
            return hits[0]
    sys.exit('database not found: ' + name + ' (set MHK_HOME or pass a .sqlite path)')

def kleio_home(db):
    # directory containing the 'database' directory of the sqlite db
    d = os.path.dirname(os.path.abspath(db))
    while d and d != '/':
        if os.path.basename(d) == 'database':
            return os.path.dirname(d)
        d = os.path.dirname(d)
    return os.path.dirname(os.path.abspath(db))

def local_file(kleiofile):
    rel = (kleiofile or '').replace('/kleio-home/', '').lstrip('/')
    base = os.path.basename(rel)
    hits = [h for h in glob.glob(os.path.join(MHK, 'sources', '**', base), recursive=True)
            if os.sep + 'sources' + os.sep in h]
    hits.sort(key=len)
    if hits:
        return hits[0]
    # fallback: try the relative path under mhk-home
    cand = os.path.join(MHK, rel)
    return cand if os.path.exists(cand) else ''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='project name (dehergne, china_coimbra) or path to .sqlite')
    ap.add_argument('--entity', required=True, help='entity id, e.g. deh-antoine-thomas')
    ap.add_argument('--type', default='%', help='attribute type (SQL LIKE), default all')
    ap.add_argument('--value', default='%', help='attribute value (SQL LIKE), default all')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    db = resolve_db(args.db)
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    q = '''SELECT a.id AS attr_id, a.entity AS entity_id, a.the_type, a.the_value, a.the_date,
                  e.the_line, e.the_source, s.kleiofile, s.obs AS source_desc
           FROM attributes a
           JOIN entities e ON e.id = a.id
           LEFT JOIN sources s ON s.id = e.the_source
           WHERE a.entity = ? AND a.the_type LIKE ? AND a.the_value LIKE ?'''
    rows = [dict(r) for r in con.execute(q, (args.entity, args.type, args.value))]
    con.close()
    out = []
    for r in rows:
        lf = local_file(r['kleiofile'])
        line = r['the_line']
        r['local_file'] = lf
        r['vscode'] = ('vscode://file' + lf + ':' + str(line)) if lf else ''
        rel = (r['kleiofile'] or '').replace('/kleio-home/', '')
        r['cite'] = rel + ':' + str(line)
        out.append(r)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    if not out:
        print('no attributes for', args.entity)
        return
    for r in out:
        print('entity :', r['entity_id'])
        print('attr_id:', r['attr_id'])
        print('type   :', r['the_type'], '=', r['the_value'], '| date:', r['the_date'])
        print('cite   :', r['cite'])
        print('file   :', r['local_file'] or r['kleiofile'])
        print('link   :', r['vscode'])
        print('source :', r['the_source'])
        print('desc   :', (r['source_desc'] or '').strip()[:300])
        print('-' * 70)

if __name__ == '__main__':
    main()
