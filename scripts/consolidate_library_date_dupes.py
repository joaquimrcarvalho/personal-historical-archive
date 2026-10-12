"""Consolidate the date-duplicated library folders left by the timezone bug.

Not a pha command: a one-off repair for archives that already carry the damage
described in enhancements/pha-library-slug-timezone-bug-report.md. The code fix
(F1/F2) stops NEW duplicates; this moves the content of the legacy ones into the
folder the fixed pha now uses, and parks the emptied folder under
`bin/<stamp>-library-date-dupes/` (no manifest.json, so `pha bin list` ignores
it; its README.md explains how to undo), following the convention already used
in that archive.

The "duplicate" is NOT a duplicate: the folder pha now resolves to holds the
pages (transcription + edited), while the other holds the encoder deliverables
(`records-*.json`, `concatenated-*.md`, `segments-*`, `structure.json`, filter
stamps). Deleting it would destroy the human-readable output — so every file
that is absent from (or differs from) the canonical folder is MOVED into it
first, and only files already byte-identical to the canonical copy stay behind.
After the move, every file still in the parked folder is byte-identical to its
canonical counterpart, which is what makes the move safe.

No model call and no database write (the connection is READ-ONLY): the folder
name is stamped by the next real scan/edit/encode pass, which the fixed resolver
already points at the consolidated folder.

Usage:
    python scripts/consolidate_library_date_dupes.py            # dry run
    python scripts/consolidate_library_date_dupes.py --apply
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from personal_historical_archive import db, ingest  # noqa: E402
from personal_historical_archive.config import Config  # noqa: E402


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): _digest(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def _rewrite_source(text: str, old_name: str, new_name: str) -> tuple[str, bool]:
    """Point a moved artifact's `source:` front matter at its new folder."""
    out, changed = [], False
    for line in text.split("\n"):
        if line.startswith("source:") and f"/{old_name}/" in line:
            line = line.replace(f"/{old_name}/", f"/{new_name}/")
            changed = True
        out.append(line)
    return "\n".join(out), changed


def _groups(cfg: Config, conn) -> list[tuple[dict, Path, list[Path]]]:
    out = []
    for row in db.list_documents(conn, limit=100000):
        doc = dict(row)
        canon = ingest._resolve_library_dir(cfg, doc)
        others = [f for f in ingest._existing_doc_folders(cfg, doc) if f != canon]
        if others:
            out.append((doc, canon, others))
    return out


def _free_dir(base: Path) -> Path:
    cand, n = base, 1
    while cand.exists():
        n += 1
        cand = base.with_name(f"{base.name}-{n}")
    return cand


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="perform the moves (default: dry run, nothing is written)")
    args = parser.parse_args()

    cfg = Config.load()
    conn = db.connect(cfg.db_path, readonly=True)
    groups = _groups(cfg, conn)
    if not groups:
        print("nothing to consolidate — every document has exactly one library folder")
        return

    stamp = _dt.datetime.now().strftime("%Y%m%d")
    bin_root = _free_dir(cfg.bin / f"{stamp}-library-date-dupes")
    today = _dt.date.today().isoformat()

    moved_total = binned_total = 0
    report: list[str] = []
    for doc, canon, leftovers in groups:
        print(f'\n#{doc["id"]} {doc["filename"]}')
        print(f'  canonical : {canon.name}')
        canon_tree = _tree(canon) if canon.is_dir() else {}
        for leftover in leftovers:
            left_tree = _tree(leftover)
            move = [k for k, h in left_tree.items() if canon_tree.get(k) != h]
            size = sum((leftover / k).stat().st_size for k in move)
            print(f'  duplicate : {leftover.name}')
            print(f'      mover para a canónica : {len(move)} ficheiro(s), {size/1e6:.1f} MB')
            print(f'      já lá está igual      : {len(left_tree) - len(move)}')
            if not args.apply:
                for k in move[:4]:
                    print(f'          + {k}')
                if len(move) > 4:
                    print(f'          … e mais {len(move) - 4}')
                continue
            for rel in move:
                src, dst = leftover / rel, canon / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    dst.unlink()
                if src.suffix == ".md" and "segments-" in rel:
                    text = src.read_text(encoding="utf-8")
                    text, changed = _rewrite_source(text, leftover.name, canon.name)
                    if changed:
                        dst.write_text(text, encoding="utf-8")
                        src.unlink()
                        continue
                shutil.move(str(src), str(dst))
            dest = bin_root / "library" / Path(doc["dir_path"] or "") / leftover.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(leftover), str(dest))
            moved_total += len(move)
            binned_total += 1
            rel_dest = dest.relative_to(cfg.archive_dir)
            print(f'      → {rel_dest}')
            report.append(
                f'## #{doc["id"]} {doc["filename"]}\n\n'
                f'- pasta canónica (fica): `{canon.name}`\n'
                f'- pasta aqui guardada: `{leftover.name}` — {len(move)} ficheiro(s) '
                f'foram MOVIDOS para a canónica; os {len(left_tree) - len(move)} '
                f'restantes são byte-a-byte iguais aos da canónica.\n\n'
                f'Para reverter:\n\n'
                f'    mv "{rel_dest}" \\\n'
                f'       "{Path("library") / Path(doc["dir_path"] or "") / leftover.name}"\n'
            )

    if not args.apply:
        print(f'\nDRY RUN — nada escrito. {sum(len(o) for _, _, o in groups)} pasta(s) '
              f'seriam consolidadas; backup em {bin_root.relative_to(cfg.archive_dir)}')
        return

    bin_root.mkdir(parents=True, exist_ok=True)
    (bin_root / "README.md").write_text(
        "# Pastas de library duplicadas por fuso horário — consolidadas\n\n"
        f"Movidas aqui em {today}, ao corrigir o bug do nome da pasta derivado do\n"
        f"fuso horário (pha 0.43.1; ver enhancements/pha-library-slug-timezone-bug-report.md).\n\n"
        "Cada documento tinha a MESMA versão em duas pastas com datas diferentes.\n"
        "A pasta canónica é a que tem a transcrição; a outra guardava os registos e\n"
        "os segmentos gerados. Antes de mover nada, **todos** os ficheiros que não\n"
        "existiam (ou diferiam) na canónica foram MOVIDOS para lá — por isso o que\n"
        "fica aqui é, ficheiro a ficheiro, byte-a-byte igual ao da canónica. Nada\n"
        "que exista só aqui foi perdido. Não há `manifest.json`: o `pha bin list`\n"
        "ignora esta pasta de propósito.\n\n"
        "## O que foi feito, por documento\n\n"
        + "\n".join(report) +
        "\n## Verificação\n\n"
        "Depois da consolidação, confirmar:\n\n"
        "    pha status              # library_missing tem de ficar vazio\n"
        "    pha doctor              # não deve aparecer a secção `library`\n\n",
        encoding="utf-8",
    )
    print(f'\nDONE — {moved_total} ficheiro(s) movidos para as pastas canónicas, '
          f'{binned_total} pasta(s) guardadas em {bin_root.relative_to(cfg.archive_dir)}')
    print("verificar com `pha status` (library_missing deve ficar vazio)")


if __name__ == "__main__":
    main()
