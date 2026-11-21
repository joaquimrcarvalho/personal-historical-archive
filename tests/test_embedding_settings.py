"""The embedding model is a PER-MACHINE setting.

A second machine could not switch it without editing the git-tracked
`config.yaml` (and a wheel install has no `config.yaml` at all, so it silently
ran the defaults compiled into `config.py`). These tests pin the resolution:

    per-user settings  >  project config.yaml  >  built-in defaults

key by key, with the winning source recorded on the Config (`embed_source`) so
`pha doctor` / `pha set embeddings` can state it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import cli
from personal_historical_archive.config import Config, user_settings_file

PROJECT_EMB = """embeddings:
  model: project-model
  base_url: http://127.0.0.1:1234/v1
  batch_size: 7
"""


def _project(tmp_path: Path, embeddings: str | None) -> Path:
    root = tmp_path / "proj"
    root.mkdir(parents=True)
    body = "paths:\n  archive_dir: archive\n"
    if embeddings is not None:
        body += embeddings
    (root / "config.yaml").write_text(body, encoding="utf-8")
    (root / "archive").mkdir()
    return root


def _user_settings(tmp_path: Path, body: str) -> None:
    f = user_settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")


def _ambient(monkeypatch, root: Path) -> None:
    """Load as an installed/ambient pha would (per-user settings apply)."""
    monkeypatch.setenv("PHA_HOME", str(root))
    monkeypatch.delenv("PHA_ARCHIVE_DIR", raising=False)


def test_user_embeddings_override_the_project_key_by_key(tmp_path, monkeypatch):
    root = _project(tmp_path, PROJECT_EMB)
    _user_settings(tmp_path, "embeddings:\n  model: machine-model\n")
    _ambient(monkeypatch, root)

    cfg = Config.load()

    assert cfg.embed_model == "machine-model"      # per-user wins
    assert cfg.embed_base_url == "http://127.0.0.1:1234/v1"  # project kept
    assert cfg.embed_batch_size == 7               # project kept
    assert cfg.embed_source == "user"


def test_project_config_is_the_source_without_a_user_block(tmp_path, monkeypatch):
    root = _project(tmp_path, PROJECT_EMB)
    _ambient(monkeypatch, root)

    cfg = Config.load()

    assert cfg.embed_model == "project-model"
    assert cfg.embed_source == "config"


def test_built_in_default_when_no_config_carries_embeddings(tmp_path, monkeypatch):
    root = tmp_path / "bare"
    root.mkdir()
    _ambient(monkeypatch, root)

    cfg = Config.load()

    assert cfg.embed_model == "text-embedding-nomic-embed-text-v1.5@q4_k_m"
    assert cfg.embed_source == "default"


def test_explicit_root_ignores_the_per_user_embeddings(tmp_path, monkeypatch):
    """`Config.load(root)` names a tree, so the developer's machine settings
    must not leak into it (the same rule the archive pointer follows)."""
    root = _project(tmp_path, PROJECT_EMB)
    _user_settings(tmp_path, "embeddings:\n  model: machine-model\n")

    cfg = Config.load(root)

    assert cfg.embed_model == "project-model"
    assert cfg.embed_source == "config"


def _set_args(**kw):
    base = dict(model=None, base_url=None, server=None, backend=None,
                batch_size=None, timeout_s=None, unset=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_set_embeddings_writes_the_per_user_file(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, PROJECT_EMB)
    _ambient(monkeypatch, root)

    cli.cmd_set_embeddings(Config.load(), _set_args(model="machine-model"))

    f = user_settings_file()
    assert f.is_file()
    assert "model: machine-model" in f.read_text(encoding="utf-8")
    # the tracked project config is untouched
    assert "project-model" in (root / "config.yaml").read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "pha reindex" in out
    assert Config.load().embed_model == "machine-model"


def test_set_embeddings_reports_the_effective_value(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, PROJECT_EMB)
    _ambient(monkeypatch, root)

    cli.cmd_set_embeddings(Config.load(), _set_args())

    out = capsys.readouterr().out
    assert "project-model" in out
    assert "project config.yaml" in out


def test_set_embeddings_unset_returns_to_the_project_value(tmp_path, monkeypatch):
    root = _project(tmp_path, PROJECT_EMB)
    _user_settings(tmp_path, "embeddings:\n  model: machine-model\n")
    _ambient(monkeypatch, root)
    assert Config.load().embed_model == "machine-model"

    cli.cmd_set_embeddings(Config.load(), _set_args(unset=True))

    assert Config.load().embed_model == "project-model"
    assert Config.load().embed_source == "config"


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        (dict(model=""), "must not be empty"),
        (dict(base_url="127.0.0.1:1234/v1"), "must start with http"),
        (dict(batch_size=0), "must be at least 1"),
        (dict(timeout_s=0), "must be at least 1"),
    ],
)
def test_set_embeddings_rejects_bad_values(tmp_path, monkeypatch, capsys, kw, message):
    root = _project(tmp_path, PROJECT_EMB)
    _ambient(monkeypatch, root)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_set_embeddings(Config.load(), _set_args(**kw))

    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert not user_settings_file().exists()


def test_doctor_reports_the_embedding_model_and_source():
    from personal_historical_archive import doctor

    report = doctor.diagnose(embeddings={
        "model": "machine-model",
        "base_url": "http://127.0.0.1:11434/v1",
        "source": "user",
        "source_text": "per-user settings",
    })

    assert report["embeddings"]["model"] == "machine-model"
    text = doctor.render(report)
    assert "embedding model (semantic search)" in text
    assert "machine-model" in text
    assert "per-user settings" in text


# --------------------------------------------------------------------- window
#
# The budget must follow the SERVED model, not a constant: another machine (or
# the same one, next month) may serve a 512-token embedding model. These tests
# pin the discovery order and its direction — discovery may only SHRINK the
# configured budget, never raise it.

import httpx

from personal_historical_archive import embed as embed_lib
from personal_historical_archive import model_client as mc
from personal_historical_archive.model_client import ModelClient


@pytest.fixture(autouse=True)
def _clear_window_cache():
    mc._EMBED_WINDOW_CACHE.clear()
    yield
    mc._EMBED_WINDOW_CACHE.clear()


def _client(handler) -> ModelClient:
    c = ModelClient("http://stub/v1", timeout_s=1)
    c._http.close()
    c._http = httpx.Client(transport=httpx.MockTransport(handler),
                           base_url="http://stub/v1")
    return c


def _handler(routes: dict[str, dict]):
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=body)

    return handle, calls


def test_window_comes_from_lm_studios_native_api():
    handle, _ = _handler({"/api/v0/models": {"data": [
        {"id": "other", "max_context_length": 4096},
        {"id": "nomic@q4_k_s", "type": "embeddings", "max_context_length": 2048},
    ]}})
    assert _client(handle).embedding_window("nomic@q4_k_s") == 2048


def test_window_is_quantization_agnostic():
    handle, _ = _handler({"/api/v0/models": {"data": [
        {"id": "nomic@q4_k_m", "max_context_length": 2048},
    ]}})
    assert _client(handle).embedding_window("nomic@q8_0") == 2048


def test_window_comes_from_ollama_show():
    handle, _ = _handler({"/api/show": {"model_info": {
        "nomic-bert.context_length": 8192,
        "nomic-bert.embedding_length": 768,
    }}})
    assert _client(handle).embedding_window("nomic-embed-text") == 8192


def test_window_comes_from_a_generic_model_list():
    handle, _ = _handler({"/v1/models": {"data": [
        {"id": "bge-small", "context_length": 512},
    ]}})
    assert _client(handle).embedding_window("bge-small") == 512


def test_window_is_none_when_the_server_does_not_say():
    handle, calls = _handler({})
    assert _client(handle).embedding_window("whatever") is None
    assert calls == ["/api/v0/models", "/api/show", "/v1/models"]


def test_window_probe_is_cached_per_model_and_endpoint():
    handle, calls = _handler({"/api/v0/models": {"data": [
        {"id": "m", "max_context_length": 2048},
    ]}})
    c = _client(handle)
    assert c.embedding_window("m") == 2048
    assert c.embedding_window("m") == 2048
    assert calls == ["/api/v0/models"]


class _WindowedEmbed:
    """A client whose only job is to advertise a context window."""

    def __init__(self, tokens):
        self.tokens = tokens

    def embedding_window(self, model):
        return self.tokens


def test_discovered_window_shrinks_the_configured_budget():
    cfg = SimpleNamespace(embed_max_chars=8192, embed_context_tokens=2048,
                          embed_model="m", chunk_chars=2000)
    assert embed_lib.max_input_chars(cfg, _WindowedEmbed(512)) == 2048
    assert embed_lib.chunk_size(cfg, _WindowedEmbed(512)) == 2000
    # a 256-token model leaves less room than the configured chunk size
    assert embed_lib.max_input_chars(cfg, _WindowedEmbed(256)) == 1024
    assert embed_lib.chunk_size(cfg, _WindowedEmbed(256)) == 1024


def test_a_larger_server_window_never_raises_the_configured_budget():
    cfg = SimpleNamespace(embed_max_chars=8192, embed_context_tokens=2048,
                          embed_model="m", chunk_chars=2000)
    assert embed_lib.max_input_chars(cfg, _WindowedEmbed(8192)) == 8192


def test_explicit_max_chars_caps_a_large_model_window():
    cfg = SimpleNamespace(embed_max_chars=4000, embed_context_tokens=2048,
                          embed_model="m", chunk_chars=2000)
    assert embed_lib.max_input_chars(cfg, _WindowedEmbed(8192)) == 4000
    assert embed_lib.chunk_size(cfg, _WindowedEmbed(8192)) == 2000


def test_config_parses_context_tokens_and_max_chars(tmp_path, monkeypatch):
    root = _project(tmp_path, """embeddings:
  model: project-model
  context_tokens: 512
""")
    _ambient(monkeypatch, root)
    cfg = Config.load()
    assert cfg.embed_context_tokens == 512
    assert cfg.embed_max_chars == 2048

    root = _project(tmp_path / "second", """embeddings:
  model: project-model
  context_tokens: 2048
  max_chars: 3000
""")
    _ambient(monkeypatch, root)
    assert Config.load().embed_max_chars == 3000
