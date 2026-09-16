"""The historian guide (HISTORIANS_README.md) is written for a non-programmer.

These tests pin the properties that keep it usable for its audience: it must
not put pha commands in the prose (commands belong inside the copy-paste prompt
blocks), it must present the reading methods as a ladder (builtin text, OCR
text, local vision model, remote vision model), it must tell a vision-capable
agent to test which method reads the document best, and it must name DeepSeek
and Kimi among the assistants a historian may be using.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[1] / "HISTORIANS_README.md"

# `pha <subcommand>`, with or without backticks — a command, not the project name.
_PHA_COMMAND = re.compile(
    r"`?\bpha\s+(status|scan|edit|encode|reindex|review|search|page|bib|cite|"
    r"test|doctor|key|mcp|set|init-archive|bundle|unbundle|prune|inbox|"
    r"palaeographer|editor|prompts|encoder|filters|serve|export|rm|help|info|"
    r"update|upload|migrate-config)\b"
)


def _prose_lines():
    """The lines of the guide that are NOT inside a fenced block (a prompt)."""
    in_fence = False
    for line in DOC.read_text(encoding="utf-8").split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield line


def test_no_pha_commands_in_the_prose():
    offenders = [ln.strip() for ln in _prose_lines() if _PHA_COMMAND.search(ln)]
    assert not offenders, (
        "pha commands belong inside the copy-paste prompt blocks, not the "
        "historian-facing prose: " + " | ".join(offenders)
    )


def test_reading_methods_are_presented_as_a_ladder():
    text = DOC.read_text(encoding="utf-8")
    # the ladder is its own section: slice up to the next top-level heading
    start = text.index("How your pages can be read: the ladder")
    end = text.index("\n## ", start)
    section = text[start:end].lower()
    ladder = [
        "builtin text",
        "ocr text",
        "local vision model",
        "remote vision model",
    ]
    positions = [section.find(term) for term in ladder]
    assert all(p != -1 for p in positions), "every reading rung must be named"
    assert positions == sorted(positions), (
        "the ladder must run: builtin text -> OCR text -> local vision model "
        "-> remote vision model"
    )


def test_choosing_the_rung_lives_in_the_palaeographer_step():
    """Choosing/testing the reading method is part of "3. Set up palaeographers
    and editors", not a section of its own."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index("## 3. Set up palaeographers and editors")
    end = text.index("## 4. Process the documents", start)
    assert "Choosing the rung" in text[start:end]


def test_a_vision_agent_is_asked_to_test_the_best_reading_method():
    text = DOC.read_text(encoding="utf-8")
    assert "Use your vision to read a few reference pages" in text


def test_editor_is_a_separate_choice_from_the_reader():
    """The editor works on text only: it can be a different model from the
    reader, and translating Latin/Greek needs a linguistically strong one."""
    text = DOC.read_text(encoding="utf-8")
    assert "separate choice" in text
    assert "need to see images" in text
    assert "Latin" in text and "Greek" in text


def test_the_reading_test_prompt_tells_the_agent_to_use_pha_test():
    """The 'test the reading methods' prompt must name `pha test` (the dry-run
    sample command), so the agent compares the rungs without touching the
    archive."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index("Use your vision to read a few reference pages")
    prompt = text[start:text.index("```", start)]
    assert "`pha test`" in prompt
    assert "pha test --show" in prompt
    assert "changes nothing in my archive" in prompt


@pytest.mark.parametrize("agent", ["DeepSeek", "Kimi"])
def test_guide_names_deepseek_and_kimi(agent):
    assert agent in DOC.read_text(encoding="utf-8")
