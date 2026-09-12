"""Stage filters — deterministic transformations around a pipeline stage.

A filter sits between the input and the model (``pre``) or after it
(``post``)::

    palaeographer:  page image -> [model/engine] -> (post filters) -> raw text
    editor:         raw text -> (pre filters) -> [model] -> (post filters) -> edited
    encoder:        text -> (pre filters) -> [model] -> records -> (post filters)

Which filters run, at which hook, is configured per collection/document in
``pha.yaml`` (``pre:``/``post:`` lists); the filter definitions live in ONE
archive-level ``filters/`` directory, as ``filters/<id>/filter.py`` plus an
optional ``filters/<id>/filter.md`` manifest. See ``FILTERS_PLAN.md`` for the
full design (DEC-1..DEC-5) and README "Stage filters" for the user view.

Failure is always non-destructive: a filter that raises, exits non-zero or
times out fails that unit, is recorded, and NOTHING partially filtered is
stored (the caller keeps the original value).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import types
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# --------------------------------------------------------------------------- contract

#: hooks a filter can be attached to, and the envelope `kind` each carries
HOOK_KINDS: dict[str, str] = {
    "palaeographer.post": "text",
    "editor.pre": "text",
    "editor.post": "text",
    "encoder.pre": "text",
    "encoder.post": "records",
}

_KINDS = {"text", "records"}
_ACCEPTS = {"text", "records", "any"}
_RETURNS = {"text", "records", "none"}

DEFAULT_TIMEOUT_S = 1800.0

#: a manifest is optional; without one these are the defaults
_DEFAULT_ACCEPTS = "any"
_DEFAULT_RETURNS = "text"


class FilterError(RuntimeError):
    """A filter could not be loaded, or failed while running."""


@dataclass(frozen=True)
class FilterSpec:
    """One filter reference from a ``pha.yaml`` stage block.

    ``params`` overrides the manifest's defaults for this use of the filter.
    """

    name: str
    params: dict = field(default_factory=dict)


@dataclass
class Filter:
    """A discovered filter definition (``filters/<id>/``)."""

    name: str
    path: Path
    accepts: str = _DEFAULT_ACCEPTS
    returns: str = _DEFAULT_RETURNS
    command: list[str] = field(default_factory=list)
    timeout_s: float = DEFAULT_TIMEOUT_S
    params: dict = field(default_factory=dict)
    description: str = ""
    inputs: list[str] = field(default_factory=list)
    #: files the definition consists of, for staleness/provenance
    files: list[Path] = field(default_factory=list)

    @property
    def script(self) -> Path | None:
        return self.path / "filter.py" if (self.path / "filter.py").exists() else None


# --------------------------------------------------------------------------- discovery

def _read_manifest(path: Path) -> tuple[dict, str]:
    """Parse ``filters/<id>/filter.md`` -> (front matter, description).

    Mirrors the other definition loaders: YAML front matter between ``---``
    markers, everything after it is human-readable description.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise FilterError(f"cannot read {path}: {e}") from e
    if not text.startswith("---"):
        return {}, text.strip()
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text.strip()
    fm_text = text[3:end].strip()
    body = text[end + 4:].strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        raise FilterError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(fm, dict):
        raise FilterError(f"{path}: expected a YAML mapping in the front matter")
    return fm, body


def _filter_from_dir(d: Path) -> Filter | None:
    """Build a Filter from a ``filters/<id>/`` directory (None if not one)."""
    if d.name.startswith("_") or not d.is_dir():
        return None
    manifest = d / "filter.md"
    script = d / "filter.py"
    if not manifest.exists() and not script.exists():
        return None
    fm: dict = {}
    description = ""
    if manifest.exists():
        fm, description = _read_manifest(manifest)
    accepts = str(fm.get("accepts", _DEFAULT_ACCEPTS)).strip() or _DEFAULT_ACCEPTS
    returns = str(fm.get("returns", _DEFAULT_RETURNS)).strip() or _DEFAULT_RETURNS
    if accepts not in _ACCEPTS:
        raise FilterError(f"{d.name}: accepts must be one of {sorted(_ACCEPTS)}, got {accepts!r}")
    if returns not in _RETURNS:
        raise FilterError(f"{d.name}: returns must be one of {sorted(_RETURNS)}, got {returns!r}")
    if not script.exists() and not fm.get("command"):
        raise FilterError(f"{d.name}: no filter.py and no command: in the manifest")
    params = fm.get("params") or {}
    if not isinstance(params, dict):
        raise FilterError(f"{d.name}: params: must be a mapping")
    inputs = fm.get("inputs") or []
    if isinstance(inputs, str):
        inputs = [inputs]
    if not isinstance(inputs, list):
        raise FilterError(f"{d.name}: inputs: must be a list of paths/globs")
    command = fm.get("command") or []
    if isinstance(command, str):
        command = shlex.split(command)
    files = [p for p in (manifest, script) if p.exists()]
    return Filter(
        name=d.name,
        path=d,
        accepts=accepts,
        returns=returns,
        command=[str(c) for c in command],
        timeout_s=float(fm.get("timeout_s", DEFAULT_TIMEOUT_S)),
        params=dict(params),
        description=(str(fm.get("description") or "") or description).strip(),
        inputs=[str(i) for i in inputs],
        files=files,
    )


def discover_filters(filters_dir: Path) -> dict[str, Filter]:
    """All filters in the archive's ``filters/`` directory, by id.

    A malformed manifest raises (callers that must not hard-fail, such as
    `pha status`, catch it). A missing directory is simply an empty registry.
    """
    out: dict[str, Filter] = {}
    if not filters_dir.is_dir():
        return out
    for d in sorted(filters_dir.iterdir()):
        f = _filter_from_dir(d) if d.is_dir() else None
        if f is not None:
            out[f.name] = f
    return out


def load_filter(filters_dir: Path, name: str) -> Filter:
    """The filter with this id, or a readable error."""
    f = discover_filters(filters_dir).get(name)
    if f is None:
        known = ", ".join(sorted(discover_filters(filters_dir))) or "(none)"
        raise FilterError(f"no filter named {name!r} in {filters_dir} (available: {known})")
    return f


def validate_for_hook(f: Filter, hook: str) -> None:
    """Reject a filter that cannot run at this hook (load-time error)."""
    kind = HOOK_KINDS.get(hook)
    if kind is None:
        raise FilterError(f"unknown filter hook {hook!r}")
    if f.accepts not in ("any", kind):
        raise FilterError(
            f"filter {f.name!r} accepts {f.accepts} but hook {hook} passes {kind}"
        )
    if f.returns not in ("none", kind):
        # `none` = pure side-effect filter (artifacts); otherwise it must be
        # able to return the kind the hook expects.
        raise FilterError(
            f"filter {f.name!r} returns {f.returns} but hook {hook} expects {kind} or none"
        )


# --------------------------------------------------------------------------- envelopes

def build_envelope(kind: str, value, context: dict) -> dict:
    return {"kind": kind, "value": value, "context": context}


def parse_result(raw, *, expected_kind: str, name: str) -> tuple[bool, object]:
    """Validate a filter's returned envelope.

    Returns ``(changed, value)``. An empty/None result means pass-through
    (``changed=False``) — the documented way for an artifact filter to consume
    the value, write files and leave the pipeline untouched.
    """
    if raw is None or raw == "" or raw == {}:
        return False, None
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise FilterError(f"filter {name!r} returned {type(raw).__name__}, expected an envelope")
    kind = raw.get("kind", expected_kind)
    if kind is not None and kind not in _KINDS:
        raise FilterError(f"filter {name!r} returned kind {kind!r} (expected text/records)")
    if "value" not in raw:
        # {"value": null} and a bare "pass-through" envelope are both no-ops
        return False, None
    value = raw["value"]
    if value is None:
        return False, None
    if kind != expected_kind:
        raise FilterError(
            f"filter {name!r} returned kind {kind!r} but the hook expects {expected_kind!r}"
        )
    return True, value


# --------------------------------------------------------------------------- running

def _load_module(f: Filter):
    """Import a Python filter module in-process (see the plan's §8 note).

    The cache is content-checked, not just name-checked: a module is reused
    only when the bytes it was loaded from still match the file on disk.
    Editing a filter therefore takes effect on the NEXT call in the same
    process (a long `pha scan` must not keep running a filter it has already
    superseded), and the cache name carries the content digest so two
    different filters can never share an entry.
    """
    script = f.script
    if script is None:
        raise FilterError(f"filter {f.name!r} has no filter.py to import")
    try:
        digest = hashlib.sha256(script.read_bytes()).hexdigest()
    except OSError as e:
        raise FilterError(f"filter {f.name!r}: cannot read {script}: {e}") from e
    name = f"_pha_filter_{f.name}_{digest[:12]}"
    mod = sys.modules.get(name)
    if mod is not None and getattr(mod, "__pha_source_digest__", None) == digest:
        return mod
    if mod is not None:
        # same name, different bytes (a rewritten file): reload rather than
        # run stale code
        sys.modules.pop(name, None)
    # Compile the SOURCE we just read instead of going through the import
    # machinery: a filter.py edited twice within one filesystem timestamp tick,
    # to the same size, leaves a stale __pycache__ .pyc whose (mtime, size)
    # still matches, and Python would happily execute the OLD code. Loading the
    # bytes removes that whole class of surprise.
    try:
        code = compile(script.read_bytes(), str(script), "exec")
    except (OSError, SyntaxError) as e:
        raise FilterError(f"filter {f.name!r} failed to load ({script}): {e}") from e
    mod = types.ModuleType(name)
    mod.__file__ = str(script)
    mod.__pha_source_digest__ = digest
    sys.modules[name] = mod
    try:
        exec(code, mod.__dict__)
    except Exception as e:  # noqa: BLE001 - report a filter's error clearly
        sys.modules.pop(name, None)
        raise FilterError(f"filter {f.name!r} failed to import ({script}): {e}") from e
    return mod


def _invoke_in_process(f: Filter, value, ctx: dict, *, expected_kind: str):
    mod = _load_module(f)
    run = getattr(mod, "run", None)
    if not callable(run):
        raise FilterError(f"filter {f.name!r} has no run(value, ctx) function")
    try:
        result = run(value, ctx)
    except FilterError:
        raise
    except Exception as e:  # noqa: BLE001 - a filter's error is a stage failure
        raise FilterError(f"filter {f.name!r} failed: {type(e).__name__}: {e}") from e
    # A filter may return the bare value (the ergonomic form) or an envelope.
    if isinstance(result, dict) and ("value" in result or "kind" in result):
        return parse_result(result, expected_kind=expected_kind, name=f.name)
    if result is None:
        return False, None
    return True, result


def _invoke_subprocess(f: Filter, value, ctx: dict, *, expected_kind: str,
                       tmpdir: Path | None = None):
    import tempfile

    with tempfile.TemporaryDirectory(prefix="pha-filter-", dir=str(tmpdir) if tmpdir else None) as td:
        td_path = Path(td)
        in_file = td_path / "input.json"
        out_file = td_path / "output.json"
        in_file.write_text(
            json.dumps(build_envelope(expected_kind, value, ctx), ensure_ascii=False),
            encoding="utf-8",
        )
        if f.command:
            argv = [*f.command, "--input", str(in_file), "--output", str(out_file)]
        else:
            argv = [sys.executable, "-m", "personal_historical_archive.filter_api",
                    "--input", str(in_file), "--output", str(out_file)]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=f.timeout_s,
                cwd=str(f.path), env=dict(os.environ),
            )
        except subprocess.TimeoutExpired as e:
            raise FilterError(f"filter {f.name!r} timed out after {f.timeout_s:g}s") from e
        except OSError as e:
            raise FilterError(f"filter {f.name!r} could not start ({argv[0]}): {e}") from e
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-800:]
            raise FilterError(
                f"filter {f.name!r} exited {proc.returncode}"
                + (f": {tail}" if tail else "")
            )
        raw = None
        if out_file.exists():
            raw = out_file.read_text(encoding="utf-8").strip()
        if not raw:
            raw = (proc.stdout or "").strip()
        # a filter may legitimately print nothing => pass-through
        if not raw:
            return False, None
        return parse_result(raw, expected_kind=expected_kind, name=f.name)


def resolve_params(f: Filter, spec: FilterSpec | None) -> dict:
    """Manifest defaults overridden by the sidecar's per-use params."""
    params = dict(f.params)
    if spec and spec.params:
        params.update(spec.params)
    return params


def resolve_inputs(f: Filter, archive_dir: Path) -> dict[str, str]:
    """Resolve the manifest's declared ``inputs:`` (globs allowed) to paths.

    One entry per declared pattern, so a filter looks its inputs up by the
    pattern it wrote; a glob with several matches resolves to the first.
    """
    out: dict[str, str] = {}
    for pattern in f.inputs:
        if any(c in pattern for c in "*?["):
            hits = sorted(archive_dir.glob(pattern))
        else:
            hits = [archive_dir / pattern]
        for h in hits:
            if h.exists():
                out[pattern] = str(h)
                break
    return out


def build_context(*, cfg, document, stage: str, hook: str, kind: str, params: dict,
                  inputs: dict, page: int | None = None, source_name: str | None = None,
                  encoder: str | None = None, library_dir: Path | None = None,
                  pages_dir_raw: Path | None = None, pages_dir_edited: Path | None = None,
                  records_file: Path | None = None, concatenated_file: Path | None = None,
                  sidecar: Path | None = None) -> dict:
    """The context object handed to a filter (FILTERS_PLAN.md §2.3).

    Every documented key is always present; a field that does not exist for
    this run is null.
    """
    doc = document or {}
    def _s(v):
        return str(v) if v is not None else None

    return {
        "archive_dir": str(getattr(cfg, "archive_dir", "") or ""),
        "document": _s(doc.get("path")) if isinstance(doc, dict) else None,
        "document_id": doc.get("id") if isinstance(doc, dict) else None,
        "filename": doc.get("filename") if isinstance(doc, dict) else None,
        "collection": doc.get("dir_path") if isinstance(doc, dict) else None,
        "stage": stage,
        "hook": hook,
        "kind": kind,
        "encoder": encoder,
        "page": page,
        "source_name": source_name,
        "library_dir": _s(library_dir),
        "pages_dir_raw": _s(pages_dir_raw),
        "pages_dir_edited": _s(pages_dir_edited),
        "records_file": _s(records_file),
        "concatenated_file": _s(concatenated_file),
        "sidecar": _s(sidecar),
        "params": params,
        "inputs": inputs,
    }


def run_filter(f: Filter, spec: FilterSpec | None, *, kind: str, value, ctx: dict,
               subprocess_only: bool = False):
    """Run one filter; returns ``(changed, value)``.

    Python filters run **in-process** by default: they are archive-owner code
    with the same trust level as prompt files, and a per-page interpreter start
    (30-50 ms x pages x filters) would cost minutes on a real volume — the
    concern FILTERS_PLAN.md §8 flags. A manifest ``command:`` (any language) or
    ``subprocess_only=True`` takes the documented subprocess path instead; both
    use the same envelope.
    """
    ctx = dict(ctx)
    ctx["filter"] = f.name
    if f.command or subprocess_only or f.script is None:
        return _invoke_subprocess(f, value, ctx, expected_kind=kind)
    return _invoke_in_process(f, value, ctx, expected_kind=kind)


def apply_filters(value, specs, *, hook: str, ctx: dict, filters_dir: Path,
                  verbose: bool = True):
    """Run a chain of filters in order; returns ``(value, ran)``.

    ``ran`` is the list of ``{name, params, sha}`` actually applied, for
    provenance. A failure raises ``FilterError`` (the caller must then discard
    the unit — nothing partially filtered is stored).
    """
    kind = HOOK_KINDS[hook]
    ran: list[dict] = []
    for spec in specs or []:
        f = load_filter(filters_dir, spec.name)
        validate_for_hook(f, hook)
        chain_ctx = dict(ctx)
        chain_ctx["params"] = resolve_params(f, spec)
        chain_ctx["inputs"] = resolve_inputs(f, filters_dir.parent)
        out = run_filter(f, spec, kind=kind, value=value, ctx=chain_ctx)
        changed, new_value = out
        if changed:
            value = new_value
        ran.append({"name": f.name, "params": chain_ctx["params"],
                    "sha": filter_sha(f)})
        if verbose:
            mark = "->" if changed else "="  # '=' pass-through / artifact
            print(f"    filter {f.name} {mark}", flush=True)
    return value, ran


# --------------------------------------------------------------------------- provenance

def filter_sha(f: Filter) -> str:
    """Short content hash of a filter definition (script + manifest + inputs).

    Used for provenance ("why is this text like this") and change detection.
    Declared inputs are watched; a file the filter reads without declaring is
    not (documented in the sample manifest).
    """
    h = hashlib.sha256()
    for p in sorted(f.files):
        try:
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
        except OSError:
            continue
    for pattern in sorted(f.inputs):
        h.update(pattern.encode("utf-8"))
    return h.hexdigest()[:16]


def newest_mtime(paths) -> float:
    newest = 0.0
    for p in paths:
        try:
            newest = max(newest, Path(p).stat().st_mtime)
        except OSError:
            continue
    return newest


def filters_signature(ran) -> str:
    """A stable string for the filter chain that produced a value.

    Stored on the page/edit (`filters` column) and compared against the chain
    configured NOW: a changed script hash, changed params or a removed/added
    filter all change the signature, so that stage re-runs. Returns "" for an
    empty chain (no filters), which is distinct from any real chain.
    """
    if not ran:
        return ""
    parts = []
    for r in ran:
        name = r.get("name") if isinstance(r, dict) else str(r)
        sha = r.get("sha", "") if isinstance(r, dict) else ""
        params = r.get("params") or {} if isinstance(r, dict) else {}
        try:
            ps = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            ps = str(params)
        parts.append(f"{name}:{sha}:{ps}")
    return "|".join(parts)


def filters_changed(recorded: str | None, current) -> bool:
    """Has the filter chain changed since this value was produced?

    `recorded` is the page/edit's stored signature; `current` is either the
    chain just applied or its signature. A page that never ran a filter
    (recorded NULL/"") and is configured with none is unchanged.
    """
    sig = current if isinstance(current, str) else filters_signature(current)
    return (recorded or "") != sig


# --------------------------------------------------------------------------- artifact stamps

def stamp_path(library_dir: Path, encoder: str, filter_name: str) -> Path:
    return library_dir / ".filter-stamps" / f"{encoder}.{filter_name}.stamp"


def artifact_stale(library_dir: Path, encoder: str, f: Filter, *,
                   pages_dir_edited: Path | None = None) -> bool:
    """Is this artifact filter due to re-run?

    Stale when the stamp is missing, or when any of its sources is newer than
    the stamp: the filter's own files, its declared inputs, or the EDITED page
    files it reads (so a historian's correction re-materialises the artifact).
    """
    stamp = stamp_path(library_dir, encoder, f.name)
    if not stamp.exists():
        return True
    try:
        stamp_mtime = stamp.stat().st_mtime
    except OSError:
        return True
    sources: list[Path] = list(f.files)
    archive = f.path.parent.parent
    for pattern in f.inputs:
        hits = sorted(archive.glob(pattern)) if any(c in pattern for c in "*?[") \
            else [archive / pattern]
        sources.extend(h for h in hits if h.exists())
    if pages_dir_edited and pages_dir_edited.is_dir():
        sources.extend(pages_dir_edited.glob("*.md"))
    return newest_mtime(sources) > stamp_mtime


def write_stamp(library_dir: Path, encoder: str, f: Filter) -> None:
    """Record a successful artifact run (mtime = now = 'these sources are done')."""
    p = stamp_path(library_dir, encoder, f.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"filter": f.name, "encoder": encoder, "sha": filter_sha(f),
                    "at": time.time()}, indent=2),
        encoding="utf-8",
    )


def clear_stamp(library_dir: Path, encoder: str, f: Filter) -> None:
    try:
        stamp_path(library_dir, encoder, f.name).unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- sample

FILTER_SAMPLE_MD = '''---
name: my-filter
description: one line shown by `pha filters`
accepts: text          # text | records | any
returns: text          # text | records | none   (none = write files, leave the value)
timeout_s: 1800
params:                # defaults; overridden per use in pha.yaml
  example: value
inputs: []             # files/globs this filter reads (watched for staleness)
# command: ["node", "index.js"]   # optional: any executable instead of filter.py
---
What this filter does, in prose. A filter receives the value produced by the
previous pipeline unit plus a context dict, and returns the new value (or None
to leave it unchanged).

Which filters run is set in the document/collection `pha.yaml`, never here:

    editor:
      rules: modernise
      model: deepseek-v4-flash
      pre: [my-filter]                       # before the model
      post:                                 # after it
        - {name: my-filter, params: {example: other}}

Hooks: `palaeographer.post`, `editor.pre`, `editor.post`, `encoder.pre`,
`encoder.post`.

A filter is arbitrary Python run by the archive owner (same trust as a prompt
file): there is NO sandbox. Files it reads but does not declare under `inputs:`
are not watched, so its output can go stale silently.
'''

FILTER_SAMPLE_PY = '''"""Template for a pha stage filter. See filter.md in this folder.

Copy this directory to filters/<my-filter>/ and rename the id. The value is
the text (or records) produced by the previous unit; the context carries the
document, page, params and resolved inputs.
"""


def run(value, ctx):
    # value: str for text hooks, list[dict] for encoder.post
    # ctx:   archive_dir, document, document_id, filename, collection, stage,
    #        hook, page, source_name, library_dir, pages_dir_raw,
    #        pages_dir_edited, records_file, concatenated_file, sidecar,
    #        params, inputs   (plus 'filter')
    params = ctx.get("params") or {}
    out = value
    if isinstance(out, str):
        out = out.replace("\\r\\n", "\\n")
    # Return the new value, or None to leave it unchanged (e.g. an artifact
    # filter that only writes files).
    return out
'''
