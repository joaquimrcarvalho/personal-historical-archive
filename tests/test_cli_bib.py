"""`pha bib`, and the scan-time sync that fills the stored reference snapshot.

The properties that matter: a reference never affects document content state
(so editing one cannot trigger re-transcription), a removed or broken sidecar
clears cleanly back to the filename citation, and `--check` fails only on
sidecars that are actually wrong.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_historical_archive import bibliography
from personal_historical_archive import cli
from personal_historical_archive import db as _db
from personal_historical_archive.ingest import sync_bibliography

MODS = """<?xml version="1.0"?>
<mods xmlns="http://www.loc.gov/mods/v3" version="3.8">
  <titleInfo><title>Documentos históricos do Padroado do Oriente</title><partNumber>IV</partNumber></titleInfo>
  <name type="personal"><namePart type="family">Silva</namePart><namePart type="given">António</namePart>
    <role><roleTerm type="text">editor</roleTerm></role></name>
  <originInfo><place><placeTerm type="text">Lisboa</placeTerm></place>
    <publisher>Imprensa Nacional</publisher><dateIssued>1947</dateIssued></originInfo>
  <location><shelfLocator>BNP RES. 1234 V.</shelfLocator></location>
  <relatedItem type="series"><titleInfo><title>Doc. Padroado do Oriente</title></titleInfo></relatedItem>
  <recordInfo><recordIdentifier source="pha">1</recordIdentifier>
    <recordOrigin>human-supplied</recordOrigin></recordInfo>
</mods>
"""

DC = {
    "@context": {"dcterms": "http://purl.org/dc/terms/", "pha": "https://example.invalid/pha#"},
    "dcterms:title": "Documentos históricos do Padroado do Oriente",
    "pha:partNumber": "IV",
    "pha:placeOfPublication": "Lisboa",
    "dcterms:publisher": "Imprensa Nacional",
    "dcterms:issued": "1947",
    "pha:shelfmark": "BNP RES. 1234 V.",
    "pha:recordOrigin": "human-supplied",
}


def _args(**kw):
    base = dict(doc=None, check=False, json=True,
                to_json=False, to_bibtex=False, write=False, keep_others=False,
                qualified=False, origin=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _snapshot(cfg, doc_id):
    conn = _db.connect(cfg.db_path)
    try:
        return dict(_db.get_document(conn, doc_id))
    finally:
        conn.close()


# --------------------------------------------------------------------------- sync

def test_sync_stores_a_reference(cfg, add_document, write_sidecar):
    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    conn = _db.connect(cfg.db_path)
    try:
        stats = sync_bibliography(cfg, conn)
        row = _db.get_bibliography(conn, doc_id)
    finally:
        conn.close()
    assert stats["parsed"] == 1
    assert row["source_format"] == "mods"
    assert row["citation"].startswith("Silva, António")
    assert row["record_origin"] == "human-supplied"


def test_sync_is_idempotent_until_the_sidecar_changes(cfg, add_document, write_sidecar):
    doc_id = add_document()
    path = write_sidecar(doc_id, MODS)
    conn = _db.connect(cfg.db_path)
    try:
        assert sync_bibliography(cfg, conn)["parsed"] == 1
        second = sync_bibliography(cfg, conn)
        assert second["parsed"] == 0 and second["unchanged"] == 1
        path.write_text(MODS.replace("1947", "1950"), encoding="utf-8")
        assert sync_bibliography(cfg, conn)["parsed"] == 1
        assert "1950" in _db.get_bibliography(conn, doc_id)["citation"]
    finally:
        conn.close()


def test_sync_clears_when_the_sidecar_is_removed(cfg, add_document, write_sidecar):
    doc_id = add_document()
    path = write_sidecar(doc_id, MODS)
    conn = _db.connect(cfg.db_path)
    try:
        sync_bibliography(cfg, conn)
        assert _db.get_bibliography(conn, doc_id) is not None
        path.unlink()
        stats = sync_bibliography(cfg, conn)
        assert stats["cleared"] == 1
        assert _db.get_bibliography(conn, doc_id) is None
    finally:
        conn.close()


def test_a_broken_sidecar_stores_no_reference(cfg, add_document, write_sidecar):
    doc_id = add_document()
    write_sidecar(doc_id, "<mods> truncated")
    conn = _db.connect(cfg.db_path)
    try:
        sync_bibliography(cfg, conn)
        assert _db.get_bibliography(conn, doc_id) is None
    finally:
        conn.close()


def test_editing_a_sidecar_never_touches_document_content_state(cfg, add_document, write_sidecar):
    """A reference is metadata: it must not mark the document for re-scan."""
    doc_id = add_document()
    before = _snapshot(cfg, doc_id)
    write_sidecar(doc_id, MODS)
    conn = _db.connect(cfg.db_path)
    try:
        sync_bibliography(cfg, conn)
    finally:
        conn.close()
    after = _snapshot(cfg, doc_id)
    assert after["sha256"] == before["sha256"]
    assert after["status"] == before["status"]
    assert after["page_count"] == before["page_count"]
    assert after["updated_at"] == before["updated_at"]


def test_sync_picks_up_a_dublin_core_sidecar(cfg, add_document, write_sidecar):
    doc_id = add_document()
    write_sidecar(doc_id, json.dumps(DC), suffix=".dc.json")
    conn = _db.connect(cfg.db_path)
    try:
        stats = sync_bibliography(cfg, conn)
        row = _db.get_bibliography(conn, doc_id)
    finally:
        conn.close()
    assert stats["parsed"] == 1
    assert row["source_format"] == "dc"
    assert "Imprensa Nacional" in row["citation"]


def test_sync_reports_a_two_sidecar_conflict(cfg, add_document, write_sidecar):
    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    write_sidecar(doc_id, json.dumps(DC), suffix=".dc.json")
    conn = _db.connect(cfg.db_path)
    try:
        stats = sync_bibliography(cfg, conn)
    finally:
        conn.close()
    assert any("more than one bibliographic sidecar" in w[2] for w in stats["warnings"])
    # the JSON sidecar wins, deterministically: it is the one a human edits
    conn = _db.connect(cfg.db_path)
    try:
        assert _db.get_bibliography(conn, doc_id)["source_format"] == "dc"
    finally:
        conn.close()


# --------------------------------------------------------------------------- pha bib

def test_bib_summarises_coverage(cfg, add_document, write_sidecar, capsys):
    with_ref = add_document(filename="a.pdf")
    add_document(filename="b.pdf")
    write_sidecar(with_ref, MODS)
    cli.cmd_bib(cfg, _args(json=False))
    out = capsys.readouterr().out
    assert "1/2 documents" in out
    assert "b.pdf" in out and "no reference (1)" in out


def test_bib_shows_one_document_field_by_field(cfg, add_document, write_sidecar, capsys):
    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    cli.cmd_bib(cfg, _args(doc=doc_id, json=False))
    out = capsys.readouterr().out
    assert "reference: Silva, António" in out
    assert "volume:    IV" in out
    assert "shelfmark: BNP RES. 1234 V." in out
    assert "verified:  yes" in out


def test_bib_reports_a_document_with_no_reference(cfg, add_document, capsys):
    doc_id = add_document()
    cli.cmd_bib(cfg, _args(doc=doc_id, json=False))
    out = capsys.readouterr().out
    assert "reference: none" in out
    assert "filename-only citation" in out


def test_bib_json_has_the_entries_and_counts(cfg, add_document, write_sidecar, capsys):
    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    cli.cmd_bib(cfg, _args())
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert (data["documents"], data["referenced"], data["unreferenced"]) == (1, 1, 0)
    assert data["entries"][0]["source_format"] == "mods"


def test_bib_check_passes_when_there_is_nothing_wrong(cfg, add_document, write_sidecar, capsys):
    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    cli.cmd_bib(cfg, _args(check=True, json=False))
    assert "all readable and unambiguous" in capsys.readouterr().out


def test_bib_check_fails_on_a_broken_sidecar(cfg, add_document, write_sidecar, capsys):
    doc_id = add_document()
    write_sidecar(doc_id, "<mods> truncated")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_bib(cfg, _args(check=True, json=False))
    assert exc.value.code == 1
    assert "empty sidecar" in capsys.readouterr().out


def test_bib_check_fails_on_two_sidecars(cfg, add_document, write_sidecar, capsys):
    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    write_sidecar(doc_id, json.dumps(DC), suffix=".dc.json")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_bib(cfg, _args(check=True, json=False))
    assert exc.value.code == 1
    assert "two sidecars" in capsys.readouterr().out


def test_bib_check_does_not_fail_merely_because_a_reference_is_absent(cfg, add_document, capsys):
    """Absence is a normal state, not an error — the no-fallback rule."""
    add_document()
    cli.cmd_bib(cfg, _args(check=True, json=False))
    assert "all readable and unambiguous" in capsys.readouterr().out


def test_bib_counts_unverified_references(cfg, add_document, write_sidecar, capsys):
    doc_id = add_document()
    write_sidecar(doc_id, MODS.replace("human-supplied", "agent-drafted-unverified"))
    cli.cmd_bib(cfg, _args(json=False))
    out = capsys.readouterr().out
    assert "1 unverified" in out


def test_library_front_matter_carries_the_reference(cfg, add_document, write_sidecar):
    """A reader of a library page should know the work it belongs to."""
    from personal_historical_archive.ingest import write_document_pages

    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    conn = _db.connect(cfg.db_path)
    try:
        out_dir = write_document_pages(cfg, conn, doc_id)
    finally:
        conn.close()
    text = (out_dir / "page-001.md").read_text(encoding="utf-8")
    assert "bibliographic_reference" in text
    assert "António" in text and "BNP RES. 1234 V." in text
    assert "bibliographic_record_id" in text


def test_library_front_matter_is_unchanged_without_a_sidecar(cfg, add_document):
    from personal_historical_archive.ingest import write_document_pages

    doc_id = add_document()
    conn = _db.connect(cfg.db_path)
    try:
        out_dir = write_document_pages(cfg, conn, doc_id)
    finally:
        conn.close()
    text = (out_dir / "page-001.md").read_text(encoding="utf-8")
    assert "bibliographic_reference" not in text


# ------------------------------------------------- converting to editable JSON

def _doc_path(cfg, doc_id):
    conn = _db.connect(cfg.db_path)
    try:
        return Path(_db.get_document(conn, doc_id)["path"])
    finally:
        conn.close()


def test_bib_to_json_prints_an_editable_sidecar(cfg, add_document, write_sidecar, capsys):
    """A Zotero MODS import must not have to be hand-edited as XML."""
    doc_id = add_document()
    write_sidecar(doc_id, MODS)          # MODS, as Zotero exports it
    cli.cmd_bib(cfg, _args(doc=doc_id, to_json=True, write=False))
    data = json.loads(capsys.readouterr().out)
    assert data["title"].startswith("Documentos históricos")
    assert data["part_number"] == "IV"
    assert data["creators"] == [{"name": "Silva, António", "role": "editor"}]
    assert data["date_issued"] == "1947"
    assert data["shelfmark"] == "BNP RES. 1234 V."
    # readable keys, no JSON-LD ceremony required
    assert not any(k.startswith(("dcterms:", "pha:")) for k in data)


def test_bib_to_json_write_replaces_the_mods_sidecar(cfg, add_document, write_sidecar):
    doc_id = add_document()
    mods_path = write_sidecar(doc_id, MODS)
    cli.cmd_bib(cfg, _args(doc=doc_id, to_json=True, write=True))
    dc_path = mods_path.with_suffix("").with_suffix(".dc.json")
    assert dc_path.is_file()
    assert not mods_path.exists(), "the MODS file would win the lookup"
    bib, warning = bibliography.load_bibliography(
        {"path": str(_doc_path(cfg, doc_id)), "kind": "pdf"})
    assert warning is None
    assert bib is not None and bib.title.startswith("Documentos históricos")
    assert bib.shelfmark == "BNP RES. 1234 V."


def test_bib_to_json_write_keeps_mods_when_asked(cfg, add_document, write_sidecar):
    doc_id = add_document()
    mods_path = write_sidecar(doc_id, MODS)
    cli.cmd_bib(cfg, _args(doc=doc_id, to_json=True, write=True, keep_others=True))
    assert mods_path.exists()
    assert mods_path.with_suffix("").with_suffix(".dc.json").is_file()


def test_bib_to_json_bulk_converts_every_reference(cfg, add_document, write_sidecar, capsys):
    a = add_document(filename="a.pdf")
    b = add_document(filename="b.pdf")
    add_document(filename="c.pdf")            # no reference: skipped
    write_sidecar(a, MODS)
    write_sidecar(b, MODS)
    cli.cmd_bib(cfg, _args(to_json=True, write=True))
    out = capsys.readouterr().out
    assert "2 written as JSON" in out and "1 without a reference" in out
    for doc_id in (a, b):
        p = _doc_path(cfg, doc_id)
        assert p.with_suffix("").with_suffix(".dc.json").is_file()
        assert not p.with_suffix(".mods.xml").exists()


def test_bib_to_json_for_a_document_without_a_reference_exits(cfg, add_document, capsys):
    doc_id = add_document()
    with pytest.raises(SystemExit) as exc:
        cli.cmd_bib(cfg, _args(doc=doc_id, to_json=True))
    assert exc.value.code == 1
    assert "no reference to convert" in capsys.readouterr().err


def test_bib_to_json_qualified_emits_json_ld(cfg, add_document, write_sidecar, capsys):
    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    cli.cmd_bib(cfg, _args(doc=doc_id, to_json=True, qualified=True))
    data = json.loads(capsys.readouterr().out)
    assert data["@context"]["dcterms"] == "http://purl.org/dc/terms/"
    assert "dcterms:title" in data and "pha:shelfmark" in data


# ------------------------------------------------- agent-drafted BibTeX from a scan

def test_bib_to_bibtex_prints_a_draftable_entry(cfg, add_document, write_sidecar, capsys):
    """The point of BibTeX: an agent can draft it from the scan, no Zotero."""
    doc_id = add_document()
    write_sidecar(doc_id, MODS)
    cli.cmd_bib(cfg, _args(doc=doc_id, to_bibtex=True, origin="agent-drafted-unverified"))
    out = capsys.readouterr().out
    assert out.startswith("@book{")
    assert "title = {Documentos históricos" in out
    assert "editor = {Silva, António}" in out    # the fixture creator is an editor
    assert "year = {1947}" in out
    assert "shelfmark = {BNP RES. 1234 V.}" in out
    assert "record_origin = {agent-drafted-unverified}" in out


def test_a_drafted_bibtex_is_written_and_badged(cfg, add_document, write_sidecar, capsys):
    doc_id = add_document()
    # start from an existing reference, re-emit as a draft
    write_sidecar(doc_id, MODS)
    cli.cmd_bib(cfg, _args(doc=doc_id, to_bibtex=True, write=True,
                           origin="agent-drafted-unverified"))
    path = _doc_path(cfg, doc_id)
    bib_path = path.with_suffix(".bib")
    assert bib_path.is_file()
    assert not path.with_suffix(".mods.xml").exists()
    bib, warning = bibliography.load_bibliography({"path": str(path), "kind": "pdf"})
    assert warning is None and bib is not None
    assert bib.is_agent_drafted() is True, "a drafted reference must be badged"
    citation = bibliography.format_citation(bib, doc_id=doc_id, page_no=1,
                                            variant_label="raw", filename=path.name)
    assert citation.endswith("[unverified reference]")


def test_a_bibtex_sidecar_written_by_hand_is_read(cfg, add_document, write_sidecar, capsys):
    """The documented workflow: the agent writes the .bib itself."""
    doc_id = add_document()
    path = _doc_path(cfg, doc_id)
    path.with_suffix(".bib").write_text(
        "@book{rego1950,\n"
        "  title = {Documentação para a história das missões do padroado português do Oriente},\n"
        "  editor = {Rego, António da Silva},\n"
        "  volume = {4},\n"
        "  address = {Lisboa},\n"
        "  publisher = {Agência Geral das Colónias},\n"
        "  year = {1950},\n"
        "  record_origin = {human-supplied},\n"
        "}\n", encoding="utf-8")
    cli.cmd_bib(cfg, _args(doc=doc_id, json=False))
    out = capsys.readouterr().out
    assert "Rego, António da Silva" in out
    assert "volume:    4" in out
    assert "verified:  yes" in out
