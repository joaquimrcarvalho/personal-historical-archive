from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import subprocess

import pytest

import personal_historical_archive.model_client as mc
from personal_historical_archive.model_client import (
    ModelClient,
    ModelError,
    _clean_html_entities,
    _strip_think,
    run_liteparse,
    run_tesseract,
)

# The real resolver, captured before the autouse fixture below disables it.
_REAL_FIND_ENGINE_BINARY = mc.find_engine_binary


@pytest.fixture(autouse=True)
def _clean_engine_state(monkeypatch):
    """Keep engine-binary resolution out of the command-construction tests.

    Those tests assert the spawned argv uses the BARE binary name
    (["tesseract", ...], ["lit", "parse", ...]), so resolution must return
    None there. The dedicated resolver tests at the bottom restore the real
    function. Also clears the cached fallback dir list between tests."""
    mc._reset_engine_fallback_cache()
    monkeypatch.setattr(mc, "find_engine_binary", lambda name: None)
    yield
    mc._reset_engine_fallback_cache()


def _use_real_resolver(monkeypatch):
    """Re-enable the real find_engine_binary for the resolver tests."""
    mc._reset_engine_fallback_cache()
    monkeypatch.setattr(mc, "find_engine_binary", _REAL_FIND_ENGINE_BINARY)


def test_clean_html_entities():
    assert _clean_html_entities("a&nbsp;b&nbsp;c") == "a b c"
    assert _clean_html_entities("x &amp; y") == "x & y"
    assert _clean_html_entities("a &lt;b&gt; c") == "a <b> c"
    assert _clean_html_entities('&quot;hi&quot; &#39;x&#39;') == '"hi" \'x\''
    assert _clean_html_entities("plain text") == "plain text"
    assert _clean_html_entities("") == ""


def test_strip_think_cleans_entities():
    out = _strip_think("<think>reasoning</think>Result&nbsp;with&nbsp;spaces")
    assert out == "Result with spaces"


def test_strip_think_no_think_still_cleans():
    out = _strip_think("a&nbsp;b")
    assert out == "a b"


def test_embed_batches_large_input(monkeypatch):
    """embed() must split a large input into batch_size-sized /embeddings
    requests instead of one giant request (pha reindex can produce thousands
    of chunks, and endpoints reject an input array over their limit)."""
    client = ModelClient("http://example/v1")
    seen: list[list[str]] = []

    def fake_post(path: str, payload: dict):
        assert path == "/embeddings"
        batch = payload["input"]
        seen.append(list(batch))
        return {"data": [{"embedding": [0.0, 0.0]} for _ in batch]}

    monkeypatch.setattr(client, "_post", fake_post)
    texts = [f"chunk {i}" for i in range(5)]
    out = client.embed("m", texts, batch_size=2)
    # one request per slice, preserving input order and count
    assert [len(b) for b in seen] == [2, 2, 1]
    assert [t for b in seen for t in b] == texts
    assert len(out) == 5
    client.close()


def test_embed_single_request_when_no_batch_size(monkeypatch):
    """batch_size <= 0/None keeps the previous one-request-for-all behavior."""
    client = ModelClient("http://example/v1")
    seen: list[list[str]] = []

    def fake_post(path: str, payload: dict):
        batch = payload["input"]
        seen.append(list(batch))
        return {"data": [{"embedding": [0.0, 0.0]} for _ in batch]}

    monkeypatch.setattr(client, "_post", fake_post)
    out = client.embed("m", ["a", "b", "c"])
    assert [len(b) for b in seen] == [3]
    assert len(out) == 3
    client.close()


# --------------------------------------------------------------------------- tesseract engine

def test_run_tesseract_builds_command_and_returns_stdout(monkeypatch):
    """run_tesseract invokes the tesseract executable and returns stdout."""
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = "SIGILLVM\n"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = run_tesseract("/tmp/page.jpg", lang="por+lat", psm=6)
    assert out == "SIGILLVM"
    assert calls[0][:4] == ["tesseract", "/tmp/page.jpg", "stdout", "-l"]
    assert calls[0][4] == "por+lat"
    assert calls[0][5:] == ["--psm", "6"]


def test_run_tesseract_defaults_to_no_lang_and_no_psm(monkeypatch):
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = "text"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_tesseract("/tmp/page.jpg") == "text"
    assert calls[0] == ["tesseract", "/tmp/page.jpg", "stdout"]


def test_run_tesseract_raises_when_not_installed(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        raise FileNotFoundError("no tesseract")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ModelError, match="not installed"):
        run_tesseract("/tmp/page.jpg")


def test_run_tesseract_raises_on_nonzero_returncode(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "tesseract: unreadable image"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(ModelError, match="unreadable image"):
        run_tesseract("/tmp/page.jpg")


# --------------------------------------------------------------------------- liteparse engine

def test_run_liteparse_builds_command_and_returns_stdout(monkeypatch):
    """run_liteparse invokes `lit parse` with --ocr-language/--dpi and returns stdout."""
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = "parsed text\n"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = run_liteparse("/tmp/page.jpg", lang="por", dpi=300)
    assert out == "parsed text"
    assert calls[0] == ["lit", "parse", "/tmp/page.jpg", "--ocr-language", "por", "--dpi", "300"]


def test_run_liteparse_defaults_to_no_lang_and_no_dpi(monkeypatch):
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = "text"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_liteparse("/tmp/page.jpg") == "text"
    assert calls[0] == ["lit", "parse", "/tmp/page.jpg"]


def test_run_liteparse_raises_when_not_installed(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        raise FileNotFoundError("no lit")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ModelError, match="not installed"):
        run_liteparse("/tmp/page.jpg")


def test_run_liteparse_raises_on_nonzero_returncode(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "lit: failed to parse"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(ModelError, match="failed to parse"):
        run_liteparse("/tmp/page.jpg")


def test_run_liteparse_format_and_target_page(monkeypatch):
    """--format json/--target-pages <n> are appended when requested."""
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = '{"total_pages": 1}'
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = run_liteparse("/tmp/source.pdf", fmt="json", target_page=7, dpi=200)
    assert out == '{"total_pages": 1}'
    assert calls[0] == ["lit", "parse", "/tmp/source.pdf", "--dpi", "200",
                        "--format", "json", "--target-pages", "7"]


# --------------------------------------------------------------------------- liteparse engine routing

def _pal(**kw) -> SimpleNamespace:
    base = dict(liteparse_lang="", liteparse_dpi=None, liteparse_format="text",
                liteparse_ocr="fresh", liteparse_embedded_min_chars=200,
                liteparse_embedded_min_quality=0.60)
    base.update(kw)
    return SimpleNamespace(**base)


def test_liteparse_engine_fresh_feeds_raster(monkeypatch):
    """liteparse_ocr fresh (default) parses the rendered page raster."""
    from pathlib import Path
    from personal_historical_archive.model_client import _liteparse_page_engine

    seen: dict = {}
    monkeypatch.setattr(
        "personal_historical_archive.model_client.run_liteparse",
        lambda *a, **k: seen.update(args=a, kwargs=k) or "out",
    )
    pal = _pal(liteparse_lang="por", liteparse_dpi=300)
    ctx = SimpleNamespace(source=Path("/tmp/source.pdf"), page_no=3, total=10)
    out = _liteparse_page_engine(pal, Path("/tmp/render/p003.jpg"), ctx)
    assert out == "out"
    assert seen["args"][0] == Path("/tmp/render/p003.jpg")  # the raster, not the PDF
    assert seen["args"][1] == "por"
    assert seen["args"][2] == 300
    assert seen["kwargs"] == {"fmt": "text"}  # fresh: no --target-pages; text fmt -> no --format


def test_liteparse_engine_embedded_feeds_source_pdf_page(monkeypatch, tmp_path):
    """liteparse_ocr embedded parses the SOURCE PDF page via --target-pages."""
    from pathlib import Path
    from personal_historical_archive.model_client import _liteparse_page_engine

    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4 fake")
    seen: dict = {}
    monkeypatch.setattr(
        "personal_historical_archive.model_client.run_liteparse",
        lambda *a, **k: seen.update(args=a, kwargs=k) or "out",
    )
    pal = _pal(liteparse_ocr="embedded", liteparse_format="json", liteparse_lang="lat")
    ctx = SimpleNamespace(source=src_pdf, page_no=3, total=10)
    out = _liteparse_page_engine(pal, Path("/tmp/render/p003.jpg"), ctx)
    assert out == "out"
    assert seen["args"][0] == src_pdf  # the source PDF, not the raster
    assert seen["args"][1] == "lat"
    assert seen["kwargs"] == {"fmt": "json", "target_page": 3}


def test_liteparse_engine_embedded_non_pdf_falls_back_to_raster(monkeypatch):
    """embedded on a non-PDF source (image) has no text layer -> fresh raster."""
    from pathlib import Path
    from personal_historical_archive.model_client import _liteparse_page_engine

    seen: dict = {}
    monkeypatch.setattr(
        "personal_historical_archive.model_client.run_liteparse",
        lambda *a, **k: seen.update(args=a, kwargs=k) or "out",
    )
    pal = _pal(liteparse_ocr="embedded")
    ctx = SimpleNamespace(source=Path("/tmp/source.png"), page_no=1, total=1)
    _liteparse_page_engine(pal, Path("/tmp/render/p001.jpg"), ctx)
    assert seen["args"][0] == Path("/tmp/render/p001.jpg")
    assert seen["kwargs"] == {"fmt": "text"}


# --------------------------------------------------------------------------- embedded-layer quality gate

# ~270 chars of ordinary prose: passes every gate.
_GOOD_PROSE = (
    "The quick brown fox jumps over the lazy dog near the river bank. "
    "A second sentence follows so the page carries enough text to judge. "
    "Kept in Latin script with ordinary vowels in every single token. "
) * 2

# The same length of OCR glyph soup: consonants, digits and no vowels at all.
_GLYPH_SOUP = (
    "bb1 ccc ddd fff ggg hhh jjj kkk lll mmm nnn ppp qqq rrr sss ttt vvv "
    "www xxx zzz bb1 ccc ddd fff ggg hhh jjj kkk lll mmm nnn ppp qqq "
) * 3


def test_embedded_text_is_good_accepts_prose():
    assert mc.embedded_text_is_good(_GOOD_PROSE) is True


def test_embedded_text_is_good_rejects_short_layer():
    """Too little text on the page -> not worth reusing; OCR instead."""
    assert mc.embedded_text_is_good("Chapter One") is False
    assert mc.embedded_text_is_good(_GOOD_PROSE, min_chars=10_000) is False


def test_embedded_text_is_good_rejects_glyph_soup():
    """Long enough and mostly letters, but the tokens are not words."""
    assert len("".join(_GLYPH_SOUP.split())) >= 200  # clears the length gate,
    assert mc.embedded_text_is_good(_GLYPH_SOUP) is False  # so the word gate rejects it


def test_embedded_text_is_good_rejects_control_and_replacement_chars():
    assert mc.embedded_text_is_good(_GOOD_PROSE + "\x07") is False
    assert mc.embedded_text_is_good(_GOOD_PROSE + "\ufffd") is False


def test_embedded_text_is_good_rejects_non_letter_text():
    assert mc.embedded_text_is_good("1234567890 " * 40) is False


def test_embedded_text_is_good_accepts_non_latin_script():
    """The vowel test is Latin-only: CJK pages must not be rejected for it."""
    assert mc.embedded_text_is_good("漢字文獻檔案歷史研究" * 20) is True


def test_embedded_text_is_good_quality_threshold_is_tunable():
    """A layer that passes at the default floor can be rejected by a stricter one."""
    assert mc.embedded_text_is_good(_GOOD_PROSE, min_quality=0.60) is True
    assert mc.embedded_text_is_good(_GOOD_PROSE, min_quality=0.999) is False


def test_pdf_page_text_returns_empty_for_missing_pdf():
    assert mc.pdf_page_text("/tmp/does-not-exist-pha.pdf", 1) == ""


def test_liteparse_engine_prefer_embedded_uses_good_layer(monkeypatch, tmp_path):
    """prefer-embedded parses the SOURCE PDF when the layer reads well."""
    from pathlib import Path
    from personal_historical_archive import model_client as mc2

    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(mc2, "pdf_page_text", lambda s, p: _GOOD_PROSE)
    seen: dict = {}
    monkeypatch.setattr(
        mc2, "run_liteparse", lambda *a, **k: seen.update(args=a, kwargs=k) or "out"
    )
    pal = _pal(liteparse_ocr="prefer-embedded", liteparse_lang="por")
    ctx = SimpleNamespace(source=src_pdf, page_no=3, total=10)
    out = mc2._liteparse_page_engine(pal, Path("/tmp/render/p003.jpg"), ctx)
    assert out == "out"
    assert seen["args"][0] == src_pdf  # source PDF, not the raster
    assert seen["kwargs"] == {"fmt": "text", "target_page": 3}


def test_liteparse_engine_prefer_embedded_falls_back_on_soup(monkeypatch, tmp_path):
    """A junk embedded layer must NOT be reused -> OCR the raster."""
    from pathlib import Path
    from personal_historical_archive import model_client as mc2

    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(mc2, "pdf_page_text", lambda s, p: _GLYPH_SOUP)
    seen: dict = {}
    monkeypatch.setattr(
        mc2, "run_liteparse", lambda *a, **k: seen.update(args=a, kwargs=k) or "out"
    )
    pal = _pal(liteparse_ocr="prefer-embedded")
    ctx = SimpleNamespace(source=src_pdf, page_no=3, total=10)
    mc2._liteparse_page_engine(pal, Path("/tmp/render/p003.jpg"), ctx)
    assert seen["args"][0] == Path("/tmp/render/p003.jpg")
    assert seen["kwargs"] == {"fmt": "text"}  # no --target-pages


def test_liteparse_engine_prefer_embedded_falls_back_without_layer(monkeypatch, tmp_path):
    """A page with no embedded text at all -> OCR the raster."""
    from pathlib import Path
    from personal_historical_archive import model_client as mc2

    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(mc2, "pdf_page_text", lambda s, p: "")
    seen: dict = {}
    monkeypatch.setattr(
        mc2, "run_liteparse", lambda *a, **k: seen.update(args=a, kwargs=k) or "out"
    )
    pal = _pal(liteparse_ocr="prefer-embedded")
    ctx = SimpleNamespace(source=src_pdf, page_no=1, total=1)
    mc2._liteparse_page_engine(pal, Path("/tmp/render/p001.jpg"), ctx)
    assert seen["args"][0] == Path("/tmp/render/p001.jpg")


def test_liteparse_engine_prefer_embedded_honours_strict_threshold(monkeypatch, tmp_path):
    """A model can demand more text than the page has -> OCR the raster."""
    from pathlib import Path
    from personal_historical_archive import model_client as mc2

    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(mc2, "pdf_page_text", lambda s, p: _GOOD_PROSE)
    seen: dict = {}
    monkeypatch.setattr(
        mc2, "run_liteparse", lambda *a, **k: seen.update(args=a, kwargs=k) or "out"
    )
    pal = _pal(liteparse_ocr="prefer-embedded", liteparse_embedded_min_chars=100_000)
    ctx = SimpleNamespace(source=src_pdf, page_no=1, total=1)
    mc2._liteparse_page_engine(pal, Path("/tmp/render/p001.jpg"), ctx)
    assert seen["args"][0] == Path("/tmp/render/p001.jpg")


def test_liteparse_engine_prefer_embedded_non_pdf_never_reads_layer(monkeypatch):
    """Non-PDF source: the gate is not even consulted; OCR the raster."""
    from pathlib import Path
    from personal_historical_archive import model_client as mc2

    def _boom(*a, **k):  # must not be called
        raise AssertionError("pdf_page_text must not run for a non-PDF source")

    monkeypatch.setattr(mc2, "pdf_page_text", _boom)
    seen: dict = {}
    monkeypatch.setattr(
        mc2, "run_liteparse", lambda *a, **k: seen.update(args=a, kwargs=k) or "out"
    )
    pal = _pal(liteparse_ocr="prefer-embedded")
    ctx = SimpleNamespace(source=Path("/tmp/source.png"), page_no=1, total=1)
    mc2._liteparse_page_engine(pal, Path("/tmp/render/p001.jpg"), ctx)
    assert seen["args"][0] == Path("/tmp/render/p001.jpg")


# --------------------------------------------------------------------------- engine binary resolution

def test_find_engine_binary_prefers_path(monkeypatch):
    monkeypatch.setattr(mc, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/" + name))
    _use_real_resolver(monkeypatch)
    assert mc.find_engine_binary("tesseract") == "/usr/bin/tesseract"


def test_find_engine_binary_uses_pha_engine_path(monkeypatch, tmp_path):
    exe = tmp_path / "lit"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(mc, "shutil", SimpleNamespace(which=lambda name: None))
    monkeypatch.setenv("PHA_ENGINE_PATH", str(tmp_path))
    monkeypatch.setenv("PHA_NO_LOGIN_PATH", "1")
    _use_real_resolver(monkeypatch)
    assert mc.find_engine_binary("lit") == str(exe)


def test_find_engine_binary_uses_login_shell_path(monkeypatch, tmp_path):
    exe = tmp_path / "tesseract"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(mc, "shutil", SimpleNamespace(which=lambda name: None))
    monkeypatch.setenv("PHA_ENGINE_PATH", "")
    monkeypatch.delenv("PHA_NO_LOGIN_PATH", raising=False)
    monkeypatch.setattr(mc, "_login_shell_path", lambda: str(tmp_path))
    _use_real_resolver(monkeypatch)
    assert mc.find_engine_binary("tesseract") == str(exe)


def test_find_engine_binary_none_when_nowhere(monkeypatch):
    monkeypatch.setattr(mc, "shutil", SimpleNamespace(which=lambda name: None))
    monkeypatch.setattr(mc, "_fallback_bin_dirs", lambda: [])
    _use_real_resolver(monkeypatch)
    assert mc.find_engine_binary("tesseract") is None


def test_login_shell_path_parses_markers(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("PHA_NO_LOGIN_PATH", raising=False)

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd[0] == "/bin/zsh"
        assert "-l" in cmd and "-i" in cmd
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout="\n__PHA_PATH_START__\n/a:/b\n__PHA_PATH_END__\n",
            stderr="",
        )

    monkeypatch.setattr(mc.subprocess, "run", fake_run)
    assert mc._login_shell_path() == "/a:/b"


def test_login_shell_path_ignores_rc_noise(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("PHA_NO_LOGIN_PATH", raising=False)

    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout="noise\n__PHA_PATH_START__\n/one\n__PHA_PATH_END__\nmore\n",
            stderr="pyenv warning\n",
        )

    monkeypatch.setattr(mc.subprocess, "run", fake_run)
    assert mc._login_shell_path() == "/one"


def test_login_shell_path_opt_out_and_no_shell(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PHA_NO_LOGIN_PATH", "1")
    assert mc._login_shell_path() is None  # opt-out: must not spawn a shell
    monkeypatch.delenv("PHA_NO_LOGIN_PATH")
    monkeypatch.delenv("SHELL")
    assert mc._login_shell_path() is None


def test_login_shell_path_timeout_returns_none(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("PHA_NO_LOGIN_PATH", raising=False)

    def boom(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(mc.subprocess, "run", boom)
    assert mc._login_shell_path() is None
