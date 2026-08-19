"""`pha encoder new` — interactive wizard that lets a non-technical user
create an encoder file (encoders/<id>.md) by answering plain-language
questions, with real grounding validation and direct file writing.

Design (explored with the user):
- layer 1 (interview) is also available as a chat prompt in
  prompts/encoder-helper.md; this module is the CLI counterpart with
  machine-checked validation instead of prompt discipline.
- the encoder model drafts classes/attributes and the example JSON; the
  wizard then VERIFIES every extraction text/attribute against the sample
  (exact/verbatim check with fuzzy fallback) and asks the user to fix
  mismatches — this is what makes auto-generated examples trustworthy.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from .config import Config, _expand
from .ingest import _parse_json_array
from .model_client import ModelClient


# --------------------------------------------------------------------------- helpers

def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        raw = ""
    return raw or (default or "")


def _ask_yesno(prompt: str, default: bool = True) -> bool:
    ans = _ask(prompt, "y" if default else "n").lower()
    return ans.startswith("y")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _grounded(value, sample: str) -> bool:
    """True if `value` appears (verbatim-ish) in the sample text."""
    if value is None:
        return True
    value = str(value)
    if not value.strip():
        return True
    if _norm(value) in _norm(sample):
        return True
    # fuzzy fallback (LangExtract-style): ratio against the best window
    nv, ns = _norm(value), _norm(sample)
    if len(nv) < 4 or len(ns) < 4:
        return False
    best = 0.0
    for i in range(0, max(1, len(ns) - len(nv)), max(1, len(nv) // 2)):
        window = ns[i : i + len(nv) * 2]
        best = max(best, difflib.SequenceMatcher(None, nv, window).ratio())
        if best >= 0.9:
            return True
    return best >= 0.9


def _text_models(cfg: Config) -> list[dict]:
    """Known text models from encoders/ and editors/ (dedupe by base_url+model).

    Keeps the RAW api_key string from each file's front matter (e.g.
    "${MINIMAX_API_KEY}") — the Config objects store the EXPANDED key, which
    must never be written into a generated encoder file."""
    import yaml

    seen: dict[tuple, dict] = {}
    for m in list(cfg.encoders.values()) + list(cfg.editors.values()):
        key = (m.base_url, m.model)
        raw_key = m.api_key
        if m.prompt_file and m.prompt_file.exists():
            try:
                text = m.prompt_file.read_text(encoding="utf-8")
                fm = text.split("---", 2)[1] if text.startswith("---") else ""
                parsed = yaml.safe_load(fm) or {}
                raw_key = str(parsed.get("api_key", raw_key))
            except Exception:  # noqa: BLE001 - keep expanded key on parse failure
                pass
        seen.setdefault(key, {
            "base_url": m.base_url,
            "model": m.model,
            "api_key": raw_key,
            "temperature": m.temperature,
            "max_tokens": m.max_tokens,
            "timeout_s": m.timeout_s,
            "thinking": m.thinking,
            "api_style": m.api_style,
        })
    return list(seen.values())


def _pick_model(cfg: Config) -> dict:
    models = _text_models(cfg)
    print("Pick the TEXT model this encoder will use:")
    for i, m in enumerate(models, 1):
        print(f"  {i}. {m['model']}  ({m['base_url']})")
    print(f"  {len(models) + 1}. custom (enter endpoint/model yourself)")
    while True:
        raw = _ask("Choice", str(1))
        try:
            idx = int(raw)
        except ValueError:
            print("  (enter a number)")
            continue
        if 1 <= idx <= len(models):
            return dict(models[idx - 1])
        if idx == len(models) + 1:
            base_url = _ask("API base URL", "https://api.minimax.io/v1")
            model = _ask("Model id", "MiniMax-M2.5")
            api_key = _ask("API key (or ${ENV_VAR}, empty for local)")
            api_style = _ask("API style (openai|anthropic)", "openai")
            return {
                "base_url": base_url, "model": model, "api_key": api_key,
                "temperature": 0.0, "max_tokens": 4096, "timeout_s": 600,
                "thinking": False, "api_style": api_style,
            }
        print("  (out of range)")


def _ask_multiline(prompt: str) -> str:
    """Read a multi-line paste: first line may answer 'pick'; further lines
    are appended until an EMPTY line is entered."""
    print(prompt)
    print("(paste the text, then press Enter on an empty line to finish)", flush=True)
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


def _pick_sample(cfg: Config) -> str:
    """Paste text, or pick a page from a document in the library."""
    from . import db
    ans = _ask("Paste a sample passage, or type 'pick' to take one from a document")
    if ans.lower() != "pick":
        # the first line was the answer; keep reading the rest of the paste
        rest: list[str] = []
        try:
            while True:
                line = input()
                if line == "":
                    break
                rest.append(line)
        except EOFError:
            pass
        return "\n".join([ans, *rest]).strip()
    conn = db.connect(cfg.db_path)
    try:
        docs = db.list_documents(conn, limit=100)
        docs = [d for d in docs if d["status"] == "done"]
        if not docs:
            print("  (no extracted documents in the library — paste text instead)")
            return ""
        print("Documents in the library:")
        for i, d in enumerate(docs, 1):
            print(f"  {i}. {d['filename']}")
        while True:
            raw = _ask("Document", str(1))
            try:
                di = int(raw) - 1
                doc = docs[di]
                break
            except (ValueError, IndexError):
                print("  (invalid)")
        pages = db.get_pages(conn, doc["id"])
        pages = [p for p in pages if p["status"] == "done"]
        edits = {e["page_id"]: e["text"] for e in db.edits_for_document(conn, doc["id"], doc["editor"])
                 if doc["editor"] and e["status"] == "done" and e["text"]}
        print(f"Pages: {pages[0]['page_no']}–{pages[-1]['page_no']} — enter a page number:")
        while True:
            raw = _ask("Page")
            try:
                pno = int(raw)
            except ValueError:
                print("  (enter a number)")
                continue
            page = next((p for p in pages if p["page_no"] == pno), None)
            if page is None:
                print(f"  (no page {pno})")
                continue
            text = (edits.get(page["id"]) or page["raw_text"] or "").strip()
            print(f"--- page {pno} ---")
            print(text[:600] + (" …" if len(text) > 600 else ""))
            if _ask_yesno("Use this as the sample", True):
                return text
    finally:
        conn.close()


# --------------------------------------------------------------------------- model-assisted steps

_PROPOSE_PROMPT = """\
You are designing a structured-extraction configuration for historical documents.
Given a sample passage from the user's collection, propose the extraction
CLASSES visible in it (what things matter: e.g. letter, person, date, place,
amount) and, for each class, the ATTRIBUTES to record about it.

Sample text:
<sample>

Reply with JSON ONLY:
{"classes": [{"class": "<lowercase name>", "attributes": ["<attr>", ...]}]}
2-4 classes, lowercase names, 1-6 attributes each. No commentary.
"""

_DRAFT_PROMPT = """\
Produce the extraction example for this sample in LangExtract flat form.

Classes and their attributes:
<classes>

Sample text:
<sample>

IMPORTANT: every extraction text and every attribute value must be EXACT
text from the sample — same spelling, no modernization, no normalization
(only 'page'-style numeric attributes may be plain numbers). Do not invent
text that is not in the sample.

Reply with JSON ONLY, an array of items, one per class:
[{"<class>": "<exact text from sample>", "<class>_attributes": {"<attr>": <value>}}]
"""


def _call_model(model: dict, prompt: str) -> str:
    # the api_key in `model` is the RAW front-matter string (e.g.
    # "${MINIMAX_API_KEY}") — expand it for the live call so the generated
    # encoder file never contains the resolved secret
    client = ModelClient(model["base_url"], timeout_s=model.get("timeout_s", 600),
                         api_key=_expand(str(model.get("api_key") or "")) or None,
                         api_style=model.get("api_style", "openai"))
    try:
        # generous max_tokens: reasoning models emit a long <think> block even
        # with thinking disabled; a small cap truncates it and yields an empty
        # answer. 8192 leaves room for the think block + the real JSON answer.
        return client.chat_text(model["model"], prompt,
                                temperature=model.get("temperature", 0.0),
                                max_tokens=max(8192, int(model.get("max_tokens", 4096))),
                                thinking=model.get("thinking", True))
    finally:
        client.close()


def _propose_classes(model: dict, sample: str) -> list[dict]:
    try:
        out = _call_model(model, _PROPOSE_PROMPT.replace("<sample>", sample))
        data = _parse_json_array(out)
        if data and isinstance(data, list):
            return [c for c in data if isinstance(c, dict) and c.get("class")]
    except Exception as e:  # noqa: BLE001 - fall back to manual entry
        print(f"  (model proposal failed: {e}; enter classes manually)")
    return []


def _draft_example(model: dict, sample: str, classes: list[dict]) -> list:
    lines = []
    for c in classes:
        attrs = c.get("attributes") or []
        lines.append(f"- {c['class']}: {', '.join(str(a) for a in attrs)}")
    try:
        out = _call_model(model, _DRAFT_PROMPT
                          .replace("<classes>", "\n".join(lines))
                          .replace("<sample>", sample))
        data = _parse_json_array(out)
        if data and isinstance(data, list):
            return data
        print("  (model returned no JSON array — example will be empty)")
    except Exception as e:  # noqa: BLE001
        print(f"  (model draft failed: {e}; example will be empty)")
    return []


def _validate_example(sample: str, items: list) -> list[str]:
    """Return human-readable grounding issues for a drafted example."""
    issues: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for k, v in item.items():
            if k.endswith("_attributes") or k.endswith("_index"):
                continue
            if isinstance(v, str) and not _grounded(v, sample):
                issues.append(f"  ✗ {k} text not found verbatim in the sample: {v!r}")
            elif isinstance(v, str):
                issues.append(f"  ✓ {k}: {v!r}")
        for k, v in item.items():
            if not k.endswith("_attributes") or not isinstance(v, dict):
                continue
            cls = k[: -len("_attributes")]
            for an, av in v.items():
                if isinstance(av, str) and not _grounded(av, sample):
                    issues.append(f"  ✗ {cls}.{an} not found verbatim: {av!r}")
    return issues


# --------------------------------------------------------------------------- file writing

_FRONT_MATTER = """\
---
description: {description}
base_url: {base_url}
model: {model}
api_key: "{api_key}"
temperature: {temperature}
max_tokens: {max_tokens}
timeout_s: {timeout_s}
thinking: {thinking}
api_style: {api_style}
batch_pages: 20
context_tokens: 200000
overlap_pages: 4
extraction_passes: 2
---
"""

_BODY_TEMPLATE = """\
You are a scholarly encoder for {description}. You are given a document as
ONE CONCATENATED text with '--- page N ---' markers between pages — records
may span several pages, so read the whole text before deciding.

{instructions}

Use EXACT TEXT from the input for every extracted value and attribute — do
not paraphrase, modernize or expand abbreviations. List records in order of
appearance. Output a JSON array of extraction items in the flat form:

  {{"<class>": "<exact text>", "<class>_attributes": {{<attribute>: <value>}}}}

One item per class (e.g. "person", "letter"), so the array may mix classes.
Output ONLY the JSON array, with no preamble or commentary.

## Examples

{examples}
"""


def _render_examples(samples: list[tuple[str, list]]) -> str:
    blocks = []
    for sample, items in samples:
        blocks.append(f"Q: {sample}\n\nA:\n{json.dumps(items, ensure_ascii=False, indent=2)}")
    return "\n\n".join(blocks)


def _instructions_from_classes(classes: list[dict]) -> str:
    lines = ["Extract the following:"]
    for c in classes:
        attrs = ", ".join(str(a) for a in (c.get("attributes") or []))
        extra = c.get("extra") or ""
        lines.append(f"- {c['class']}: {attrs}{(' — ' + extra) if extra else ''}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- main

def run(cfg: Config) -> int:
    print("pha encoder new — create an encoder by answering a few questions.\n")

    enc_id = _ask("Encoder id (file name, e.g. 'letters')", "letters").strip()
    if not re.match(r"^[a-z0-9_-]+$", enc_id):
        print(f"error: invalid id {enc_id!r} (lowercase letters, digits, _ or -)")
        return 1

    description = _ask("One-line description of what this encoder extracts",
                       "letter metadata and correspondents")
    model = _pick_model(cfg)

    # per-sample interview
    samples: list[tuple[str, list]] = []
    classes: list[dict] = []
    want_more = True
    while want_more:
        sample = _pick_sample(cfg)
        if not sample:
            print("  (no sample — cannot build an example; aborting)")
            return 1
        if len(samples) >= 1:
            print("Reusing the classes you confirmed earlier. Change them?")
        if not classes:
            proposed = _propose_classes(model, sample)
            if proposed:
                names = ", ".join(c["class"] for c in proposed)
                print(f"From this sample I can see: {names}.")
                print("Keep these, or enter your own (comma-separated class names)?")
                keep = _ask("Classes", ",".join(c["class"] for c in proposed))
                kept_names = [n.strip() for n in keep.split(",") if n.strip()]
                for name in kept_names:
                    prop = next((c for c in proposed if c["class"] == name), None)
                    if prop:
                        classes.append(prop)
                    else:
                        classes.append({"class": name, "attributes": []})
                # confirm attributes per class
                for c in classes:
                    prop = next((p for p in proposed if p["class"] == c["class"]), None)
                    if prop and prop.get("attributes"):
                        print(f"  {c['class']}: propose attributes "
                              f"{', '.join(prop['attributes'])}? (or enter your own)")
                        keep = _ask("Attributes", ",".join(prop["attributes"]))
                        c["attributes"] = [a.strip() for a in keep.split(",") if a.strip()]
                    else:
                        keep = _ask(f"Attributes for {c['class']} (comma-separated)")
                        c["attributes"] = [a.strip() for a in keep.split(",") if a.strip()]
            else:
                print("(couldn't auto-propose — enter classes manually)")
                keep = _ask("Classes (comma-separated)", "letter, person")
                for name in [n.strip() for n in keep.split(",") if n.strip()]:
                    attrs = _ask(f"Attributes for {name} (comma-separated)")
                    classes.append({"class": name,
                                    "attributes": [a.strip() for a in attrs.split(",") if a.strip()]})

        print(f"\nDrafting the example for this sample (model call)...")
        items = _draft_example(model, sample, classes)
        if items:
            issues = _validate_example(sample, items)
            for line in issues:
                print(line)
            fixes = 0
            while any(line.startswith("  ✗") for line in issues) and fixes < 3:
                if _ask_yesno("\nSome values don't match the sample. Re-draft with feedback?", True):
                    # append the issues as feedback and retry
                    fb = _DRAFT_PROMPT.replace("<sample>", sample)
                    fb = fb.replace(
                        "<classes>",
                        "\n".join(f"- {c['class']}: {', '.join(str(a) for a in c.get('attributes') or [])}"
                                  for c in classes))
                    fb += "\n\nPrevious attempt had these problems:\n" + "\n".join(
                        line for line in issues if line.startswith("  ✗"))
                    try:
                        out = _call_model(model, fb)
                        items = _parse_json_array(out) or items
                    except Exception as e:  # noqa: BLE001
                        print(f"  (re-draft failed: {e})")
                    issues = _validate_example(sample, items)
                    for line in issues:
                        print(line)
                    fixes += 1
                else:
                    break
        samples.append((sample, items))
        want_more = _ask_yesno("\nAdd another sample (recommended for variety)?", False)

    # write the encoder file (model config + generic framing)
    examples_md = _render_examples(samples)
    instructions = _instructions_from_classes(classes)
    front = _FRONT_MATTER.format(
        description=description,
        base_url=model["base_url"],
        model=model["model"],
        api_key=model["api_key"] or "",
        temperature=model.get("temperature", 0.0),
        max_tokens=model.get("max_tokens", 4096),
        timeout_s=model.get("timeout_s", 600),
        thinking=("disabled" if not model.get("thinking", True) else "true"),
        api_style=model.get("api_style", "openai"),
    )
    enc_dir = cfg.encoders_dir
    enc_dir.mkdir(parents=True, exist_ok=True)
    out = enc_dir / f"{enc_id}.md"

    # where do the collection-specific schema + examples live?
    # default: next to the source (encoder-prompt-langextract.md) so they
    # travel with the collection; the encoder file stays generic.
    if _ask_yesno("Write collection files (selection + encoder.prompt.md + encoder-prompt-langextract.md with the examples)?", True):
        coll = _ask("Collection path under the dropbox (e.g. collections/pfister-notices)",
                    "collections/pfister-notices")
        coll_dir = cfg.dropbox / coll
        if not coll_dir.is_dir():
            print(f"  (directory {coll_dir} does not exist — creating it)")
            coll_dir.mkdir(parents=True, exist_ok=True)
        (coll_dir / "encoder").write_text(enc_id + "\n", encoding="utf-8")
        (coll_dir / "encoder.prompt.md").write_text(
            f"# Encoder prompt — {description}\n\n(optional: add collection-specific detection rules here.)\n",
            encoding="utf-8")
        (coll_dir / "encoder-prompt-langextract.md").write_text(
            f"# Encoder prompt (LangExtract format) — {description}\n\n"
            f"This file carries the collection-specific schema and few-shot examples. "
            f"It is composed AFTER the encoder base prompt and after `encoder.prompt.md` "
            f"(detection rules). It lives next to the source PDFs so it travels with the "
            f"collection.\n\n## Attributes (this source)\n\n{instructions}\n\n## Examples\n\n{examples_md}\n",
            encoding="utf-8")
        print(f"Wrote {coll_dir / 'encoder'}, {coll_dir / 'encoder.prompt.md'} and "
              f"{coll_dir / 'encoder-prompt-langextract.md'}")
        body = _BODY_TEMPLATE.format(
            description=description, instructions=instructions,
            examples="(see the collection's encoder-prompt-langextract.md)",
        )
        out.write_text(front + "\n" + body, encoding="utf-8")
        print(f"Wrote {out} (generic — examples live in the collection)")
    else:
        body = _BODY_TEMPLATE.format(description=description, instructions=instructions,
                                     examples=examples_md)
        out.write_text(front + "\n" + body, encoding="utf-8")
        print(f"\nWrote {out} (examples embedded)")

    print("\nDone. Run `pha encode --reprocess` to use the new encoder, or `pha encoder` to check it.")
    return 0
