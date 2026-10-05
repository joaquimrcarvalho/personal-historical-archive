"""Unit tests for the lmstudio-model-locality skill helper."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Loading the helper through importlib must not leave __pycache__ under
# skills/, which the archive-skill byte-for-byte tests scan.
sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "skills/lmstudio-model-locality/scripts/lmstudio_locality.py"


def _helper():
    spec = importlib.util.spec_from_file_location("lmstudio_locality", HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_local_disk_and_loaded_classification():
    mod = _helper()
    current = "local-device-id"
    ls = [
        {"modelKey": "m", "type": "llm", "path": "publisher/model", "deviceIdentifier": None},
        {"modelKey": "m", "type": "llm", "path": "remote-device-id:publisher/model",
         "deviceIdentifier": "remote-device-id"},
    ]
    ps_remote = [
        {"modelKey": "m", "identifier": "m", "status": "idle", "deviceIdentifier": "remote-device-id"},
    ]
    models = mod.collect(ls, ps_remote, current)
    assert models["m"]["disk"]["local"] is True
    assert models["m"]["disk"]["remote"] == ["remote-device-id"]
    assert models["m"]["verdict"] == "local_available_not_loaded"

    ps_local = ps_remote + [
        {"modelKey": "m", "identifier": "pha-local-m", "status": "idle", "deviceIdentifier": None},
    ]
    models = mod.collect(ls, ps_local, current)
    assert models["m"]["loaded"]["local"][0]["identifier"] == "pha-local-m"
    assert models["m"]["verdict"] == "local_loaded"


def test_this_device_id_is_treated_as_local():
    mod = _helper()
    ls = [{"modelKey": "m", "path": "p", "deviceIdentifier": "local-device-id"}]
    ps = [{"modelKey": "m", "identifier": "m", "deviceIdentifier": "local-device-id"}]
    models = mod.collect(ls, ps, "local-device-id")
    assert models["m"]["disk"]["local"] is True
    assert models["m"]["verdict"] == "local_loaded"


def test_remote_only_and_not_found_verdicts():
    mod = _helper()
    ls = [{"modelKey": "remote", "path": "remote-device-id:p", "deviceIdentifier": "remote-device-id"}]
    ps = [{"modelKey": "remote", "identifier": "remote", "deviceIdentifier": "remote-device-id"}]
    models = mod.collect(ls, ps, "local-device-id")
    assert models["remote"]["verdict"] == "remote_loaded_only"
    assert mod.collect([], [], "local-device-id") == {}


def test_suggested_identifier_is_path_safe():
    mod = _helper()
    ident = mod.suggested_identifier("qwen/qwen3-vl-8b@4bit")
    assert ident.startswith("pha-local-qwen")
    assert "/" not in ident and "@" not in ident


def test_filter_matches_model_key_and_loaded_identifier():
    mod = _helper()
    ls = [{"modelKey": "qwen/qwen3-vl-8b", "path": "p", "deviceIdentifier": None}]
    ps = [{"modelKey": "qwen/qwen3-vl-8b", "identifier": "pha-local-qwen",
           "deviceIdentifier": None}]
    models = mod.collect(ls, ps, "local-device-id")
    assert mod.filter_models(models, "qwen")
    assert mod.filter_models(models, "pha-local")
    assert mod.filter_models(models, "missing") == []
