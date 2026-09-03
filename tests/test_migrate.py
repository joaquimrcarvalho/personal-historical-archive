from pathlib import Path

from personal_historical_archive.config import Config
from personal_historical_archive.migrate import migrate_config


def _make_archive(tmp_path) -> tuple[Config, Path]:
    root = tmp_path / "proj"
    root.mkdir()
    arch = tmp_path / "arch"
    (root / "config.yaml").write_text(f"paths:\n  archive_dir: {arch}\n")
    return root, arch


def test_migrate_splits_interface_and_writes_sidecar(tmp_path):
    root, arch = _make_archive(tmp_path)
    (arch / "palaeographers").mkdir(parents=True, exist_ok=True)
    (arch / "dropbox").mkdir(parents=True, exist_ok=True)
    coll = arch / "dropbox" / "collections" / "COLX"
    coll.mkdir(parents=True, exist_ok=True)

    (arch / "palaeographers" / "cat1.md").write_text(
        "---\ndescription: cat1\nbase_url: https://api.minimax.io/v1\nmodel: MiniMax-M3\n"
        "api_key: '${MINIMAX_API_KEY}'\napi_style: openai\ntemperature: 0.1\nmax_tokens: 4096\n"
        "timeout_s: 900\nmax_vision_px: 3000\nvision_jpeg_quality: 55\n---\nTranscribe.\n"
    )
    (coll / "palaeographer").write_text("cat1\n")
    (coll / "editor").write_text("modernise\n")

    cfg = Config.load(root)
    report = migrate_config(cfg, dry_run=False, remove_selection_files=True)

    # interface moved to models/
    model_files = sorted(p.name for p in (arch / "models").iterdir() if not p.name.startswith("_"))
    assert "default.md" in model_files  # seeded on load
    assert any(name.startswith("minimax-m3") for name in model_files)

    # stage file rewritten content-only (no model — that moved to pha.yaml)
    stage = (arch / "palaeographers" / "cat1.md").read_text()
    assert "model:" not in stage
    assert "base_url" not in stage

    # sidecar written with the model, selection files removed
    sidecar = (coll / "pha.yaml").read_text()
    assert "palaeographer:" in sidecar
    assert "rules: cat1" in sidecar
    assert "model: minimax-m3" in sidecar
    assert not (coll / "palaeographer").exists()
    assert not (coll / "editor").exists()

    # reload: stage is rules-only; the model is bound at document resolution
    cfg2 = Config.load(root)
    p = cfg2.get_palaeographer("cat1")
    assert p.model == ""          # no model until bound
    assert p.model_ref == ""
    p2 = cfg2.resolve_model(p, "minimax-m3")
    assert p2.model == "MiniMax-M3"
    assert p2.max_vision_px == 3000


def test_migrate_is_idempotent(tmp_path):
    root, arch = _make_archive(tmp_path)
    (arch / "palaeographers").mkdir(parents=True, exist_ok=True)
    (arch / "palaeographers" / "p.md").write_text(
        "---\nbase_url: http://127.0.0.1:1234/v1\nmodel: qwen/qwen3-vl-8b\n---\nBody.\n"
    )
    cfg = Config.load(root)
    first = migrate_config(cfg, dry_run=False)
    second = migrate_config(cfg, dry_run=False)
    # second run finds the stage already content-only -> no further rewrites
    assert len(second.stages_rewritten) == 0
    assert first.models_created
