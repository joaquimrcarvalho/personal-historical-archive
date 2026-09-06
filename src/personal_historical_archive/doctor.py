"""pha doctor — engine dependency checks.

pha has no other probe for its local OCR/parse engines: a scan only discovers
a missing engine when the subprocess it spawns dies, surfacing as the
"not installed or not on PATH" ModelError. ``pha doctor`` (CLI) and
``pha_doctor()`` (MCP, runs on the archive machine) are the pre-flight check:
"is the binary each declared engine needs actually here, and is it the right
tool?"

Checks are deliberately engine-only: for every supported engine we look up
the executable pha spawns (``tesseract`` / ``lit``) on PATH and run a real
version probe. For ``lit`` we also guard against the common-name collision
(LLVM's test runner and others ship a ``lit``): LiteParse is confirmed by the
version string mentioning LiteParse, or by ``lit parse --help`` succeeding.

Binary presence is machine-level, so these checks work even before an archive
is configured; only the cross-reference of which model files DECLARE an
engine needs the archive's models registry.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from .model_client import find_engine_binary

# engine key (the `engine:` value in models/<id>.md, also the PAGE_ENGINES
# key) -> what pha needs to run it. `install`/`verify` are shown when the
# engine is missing/broken.
ENGINES: dict[str, dict[str, Any]] = {
    "tesseract": {
        "binary": "tesseract",
        "version_cmd": ["tesseract", "--version"],
        "install": (
            "brew install tesseract tesseract-lang (macOS; the -lang formula "
            "provides the language data, e.g. por/lat) | apt-get install "
            "tesseract-ocr tesseract-ocr-por (Debian/Ubuntu, one package per "
            "language) | choco install tesseract or the UB-Mannheim installer "
            "(Windows)"
        ),
        "verify": "tesseract --version",
    },
    "liteparse": {
        "binary": "lit",
        "version_cmd": ["lit", "--version"],
        # `lit parse --help` succeeds only for LiteParse's CLI; a bare
        # version string is ambiguous (`lit` is a common name, e.g. LLVM's
        # test runner).
        "parse_probe": ["lit", "parse", "--help"],
        "install": (
            "pip install liteparse OR npm i -g @llamaindex/liteparse — the "
            "SAME `lit` CLI either way; install with whichever toolchain you "
            "already use. pha resolves engines through your login shell's "
            "PATH, so any normal install works (PHA_ENGINE_PATH env for "
            "unusual locations). LiteParse bundles its own Tesseract."
        ),
        "verify": "lit --version (must print a LiteParse version)",
    },
}

_TIMEOUT_S = 30


def _run(cmd: list[str], timeout_s: int = _TIMEOUT_S) -> subprocess.CompletedProcess | None:
    """Run a probe command. Returns None when the binary is missing;
    otherwise the CompletedProcess (timeouts are folded into it)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        # A wedged probe is itself a diagnosis; surface it as a nonzero run.
        return subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr="timed out")


def _version_of(proc: subprocess.CompletedProcess) -> str:
    out = (proc.stdout or "") + proc.stderr
    for ln in out.splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def diagnose(
    declared: dict[str, list[str]] | None = None,
    require: set[str] | None = None,
    archive: str | None = None,
) -> dict[str, Any]:
    """Check every supported engine and return a JSON-friendly report.

    Args:
        declared: engine key -> model ids whose model file declares that
            engine (from the archive's models registry). Engines with at
            least one declaration are REQUIRED.
        require: additional engine keys to treat as required (e.g. an
            explicit `pha doctor --engine liteparse`).
        archive: the configured archive dir (for display only).

    Returns:
        {"ok": bool, "archive": str|None, "engines": [ ... ]} where each
        engine entry carries binary/found/path/version/ok/declared_by/
        required/problems plus static install+verify hints.
    """
    declared = declared or {}
    required: set[str] = set(declared) | set(require or [])

    engines: list[dict[str, Any]] = []
    for key in ENGINES:  # stable order: tesseract, liteparse
        spec = ENGINES[key]
        binary = spec["binary"]
        problems: list[str] = []
        found = False
        on_path = False
        path: str | None = None
        version = ""
        ok = False

        exe = find_engine_binary(binary)
        if exe is None:
            problems.append(
                f"{binary!r} is not installed or not reachable. pha spawns "
                f"{binary!r} as a subprocess and looked on its own PATH and on "
                f"the PATH your login shell would provide (and any dirs in the "
                f"PHA_ENGINE_PATH env var). If it is installed somewhere else, "
                f"add that dir to the PATH pha inherits, set PHA_ENGINE_PATH, "
                f"or symlink {binary!r} into a dir on one of those PATHs."
            )
        else:
            found = True
            on_path = shutil.which(binary) is not None
            path = str(exe)
            # run the probes against the RESOLVED executable, not the bare
            # name: the resolved path may sit in a fallback dir that is not on
            # pha's PATH at all.
            ver_cmd = list(spec["version_cmd"])
            ver_cmd[0] = path
            ver_proc = _run(ver_cmd)
            if ver_proc is None:
                problems.append(f"{binary!r} vanished between lookup and run")
            else:
                version = _version_of(ver_proc)
                if key == "tesseract":
                    ok = True  # the right tool; version is informational
                    if not version:
                        problems.append("`tesseract --version` produced no output")
                else:  # liteparse — guard against a different `lit`
                    is_liteparse = bool(
                        version and "liteparse" in version.lower()
                    )
                    if not is_liteparse:
                        probe_cmd = list(spec.get("parse_probe", []))
                        if probe_cmd:
                            probe_cmd[0] = path
                            probe = _run(probe_cmd)
                        else:
                            probe = None
                        is_liteparse = probe is not None and probe.returncode == 0
                    if is_liteparse:
                        ok = True
                        if not version:
                            version = "lit (LiteParse)"
                    else:
                        problems.append(
                            f"{path!r} does not look like LiteParse's `lit` "
                            f"(version output has no LiteParse marker and "
                            f"`lit parse --help` failed). `lit` is a common "
                            f"name — e.g. LLVM's test runner is also `lit` — "
                            f"and `engine: liteparse` spawns `lit parse`, so it "
                            f"needs LiteParse's lit."
                        )

        engines.append(
            {
                "engine": key,
                "binary": binary,
                "found": found,
                "on_path": on_path,
                "path": path,
                "version": version,
                "ok": ok,
                "declared_by": sorted(declared.get(key, [])),
                "required": key in required,
                "problems": problems,
                "install": spec["install"],
                "verify": spec["verify"],
            }
        )

    broken = [e["engine"] for e in engines if e["required"] and not e["ok"]]
    return {
        "ok": not broken,
        "broken": broken,
        "archive": archive,
        "engines": engines,
    }


def render(report: dict[str, Any]) -> str:
    """Human-readable rendering of a diagnose() report (for `pha doctor`)."""
    lines: list[str] = []
    lines.append("pha doctor — local OCR/parse engine check")
    lines.append(f"archive: {report.get('archive') or '(none configured)'}")
    lines.append("")
    any_declared = False
    for e in report["engines"]:
        if e["declared_by"]:
            any_declared = True
            who = ", ".join(e["declared_by"])
            lines.append(f"declared by: {e['engine']:<10} {who}")
    if not any_declared:
        lines.append("no model file declares an engine — the checks below are informational")
        lines.append("(a collection only needs an engine once its pha.yaml pairs a palaeographer")
        lines.append(" rules file with `model: tesseract` / `model: liteparse`).")
    lines.append("")
    for e in report["engines"]:
        mark = "[ok]  " if e["ok"] else "[FAIL]"
        req = " (required)" if e["required"] else ""
        loc = f" at {e['path']}" if e["path"] else ""
        ver = f" — {e['version']}" if e["version"] else ""
        via = (
            " (not on pha's PATH — found via your login shell's PATH / PHA_ENGINE_PATH)"
            if (e["found"] and not e["on_path"]) else ""
        )
        lines.append(f"{mark} {e['engine']}{req}: {e['binary']}{ver}{loc}{via}")
        for p in e["problems"]:
            lines.append(f"      {p}")
        if not e["ok"]:
            lines.append(f"      fix:   {e['install']}")
            lines.append(f"      verify: {e['verify']}")
    lines.append("")
    if report["broken"]:
        lines.append("RESULT: FAIL — missing/broken: " + ", ".join(report["broken"]))
    else:
        required = [e["engine"] for e in report["engines"] if e["required"]]
        if required:
            lines.append("RESULT: ok — every required engine is usable: " + ", ".join(required))
        else:
            lines.append("RESULT: ok (nothing required) — no engine is broken")
    return "\n".join(lines)
