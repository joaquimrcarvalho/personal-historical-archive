"""The CONFIGURED filter signature must equal what the runner RECORDS.

Regression (measured on the live archive): `apply_filters()` records the params
RESOLVED with the manifest's declared `params:`, while the configured side
recorded only the sidecar's declared params — `{}` for the normal spelling
`post: [my-filter]`. A filter whose manifest declares `params:` therefore
compared as "changed" on every pass, so the stage re-ran forever
(`#80`/`#81` stored `{...both...}` against a configured `{}`).

The fix resolves params on the configured side, and accepts the legacy
declared-only spelling on read so the fix does not re-run the whole archive once.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

from personal_historical_archive.config import Config
from personal_historical_archive.filters import (
    FilterSpec,
    apply_filters,
    build_context,
    filters_signature,
)
from personal_historical_archive.ingest import (
    _acceptable_filters_signature,
    _configured_filters_signature,
)

ROOT = Path(__file__).resolve().parents[1]
REF_FILTER = ROOT / "filters" / "join-hyphenated-words"


def _cfg(tmp_path) -> Config:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"paths:\n  archive_dir: {root / 'archive'}\n"
        "  dropbox: dropbox\n  inbox: inbox\n  library: library\n  renders: renders\n"
        "  palaeographers: palaeographers\n  editors: editors\n  encoders: encoders\n"
        "  models: models\n  prompts: prompts\n  db: archive.db\n"
    )
    cfg = Config.load(root)
    cfg.ensure_dirs()
    shutil.copytree(REF_FILTER, cfg.filters_dir / "join-hyphenated-words")
    return cfg


def _ctx(cfg: Config) -> dict:
    return build_context(
        cfg=cfg,
        document={"id": 7, "path": "/x/d.pdf", "filename": "d.pdf",
                  "dir_path": "collections/COLX"},
        stage="editor", hook="editor.pre", kind="text", params={}, inputs={},
        page=1, library_dir=Path("/tmp/lib"), pages_dir_edited=None,
        records_file=None, concatenated_file=None,
    )


def test_configured_signature_matches_what_the_runner_records(tmp_path):
    cfg = _cfg(tmp_path)
    spec = FilterSpec("join-hyphenated-words", {})   # the normal spelling: no params

    _out, ran = apply_filters("o gover-\nnador mandou", [spec], hook="editor.pre",
                              ctx=_ctx(cfg), filters_dir=cfg.filters_dir, verbose=False)
    recorded = filters_signature(ran)

    assert _configured_filters_signature(cfg, [spec]) == recorded, (
        "the configured signature must match the signature the runner stores, "
        "or the stage re-runs on every pass")


def test_the_legacy_declared_signature_is_still_accepted(tmp_path):
    cfg = _cfg(tmp_path)
    spec = FilterSpec("join-hyphenated-words", {})
    _out, ran = apply_filters("o gover-\nnador mandou", [spec], hook="editor.pre",
                              ctx=_ctx(cfg), filters_dir=cfg.filters_dir, verbose=False)
    recorded = filters_signature(ran)

    acceptable = _acceptable_filters_signature(cfg, [spec])
    assert recorded in acceptable
    # the pre-fix spelling (sidecar params only) still counts as unchanged, so the
    # fix does not force one mass re-run of everything already scanned
    assert any(a.endswith(":{}") for a in acceptable), acceptable


def test_a_real_change_is_still_stale(tmp_path):
    """Accepting the legacy form must not mask an actual change."""
    from personal_historical_archive.filters import filters_changed

    cfg = _cfg(tmp_path)
    spec = FilterSpec("join-hyphenated-words", {})
    acceptable = _acceptable_filters_signature(cfg, [spec])
    assert filters_changed("join-hyphenated-words:deadbeef16:{}", acceptable) is True
    assert filters_changed("", acceptable) is True          # added vs none
