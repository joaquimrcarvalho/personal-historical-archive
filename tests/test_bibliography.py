"""Bibliographic sidecars: both formats normalise to one record, one renderer.

See BIBLIOGRAPHY_PLAN.md. The load-bearing properties tested here are the ones
the design was chosen for: a missing sidecar degrades to exactly the old
citation, a malformed one never breaks `pha cite`, the MODS/DC pair agree, and
a machine-drafted reference is visibly marked.
"""
from __future__ import annotations

import json

from personal_historical_archive import bibliography as B

MODS = """<?xml version="1.0" encoding="UTF-8"?>
<mods xmlns="http://www.loc.gov/mods/v3" version="3.8">
  <titleInfo>
    <title>Documentos históricos do Padroado do Oriente</title>
    <partNumber>IV</partNumber>
  </titleInfo>
  <name type="personal">
    <namePart type="family">Silva</namePart>
    <namePart type="given">António</namePart>
    <role><roleTerm type="text">editor</roleTerm></role>
  </name>
  <typeOfResource>text</typeOfResource>
  <genre authority="marcgt">book</genre>
  <originInfo>
    <place><placeTerm type="text">Lisboa</placeTerm></place>
    <publisher>Imprensa Nacional</publisher>
    <dateIssued encoding="w3cdtf">1947</dateIssued>
  </originInfo>
  <language><languageTerm type="code" authority="iso639-2b">por</languageTerm></language>
  <location>
    <physicalLocation>Biblioteca Nacional de Portugal</physicalLocation>
    <shelfLocator>BNP RES. 1234 V.</shelfLocator>
  </location>
  <relatedItem type="host">
    <titleInfo><title>Documentos históricos do Padroado do Oriente</title></titleInfo>
    <part><detail type="volume"><number>4</number></detail></part>
  </relatedItem>
  <recordInfo>
    <recordIdentifier source="pha">22</recordIdentifier>
    <recordOrigin>human-supplied</recordOrigin>
  </recordInfo>
</mods>
"""

DC = {
    "@context": {
        "dc": "http://purl.org/dc/elements/1.1/",
        "dcterms": "http://purl.org/dc/terms/",
        "pha": "https://example.invalid/pha#",
    },
    "dcterms:title": "Documentos históricos do Padroado do Oriente",
    "pha:partNumber": "IV",
    "dcterms:creator": [{"pha:name": "Silva, António", "pha:role": "editor"}],
    "dcterms:type": "text",
    "pha:genre": "book",
    "pha:placeOfPublication": "Lisboa",
    "dcterms:publisher": "Imprensa Nacional",
    "dcterms:issued": "1947",
    "dcterms:language": "por",
    "pha:repository": "Biblioteca Nacional de Portugal",
    "pha:shelfmark": "BNP RES. 1234 V.",
    "dcterms:isPartOf": {
        "dcterms:title": "Documentos históricos do Padroado do Oriente",
        "pha:volumeNumber": "4",
    },
    "pha:recordId": "22",
    "pha:recordOrigin": "human-supplied",
}

#: Fields both parsers must agree on for the SAME work.
SHARED = ("title", "sub_title", "part_number", "part_name", "type", "genre",
          "place", "publisher", "date_issued", "edition", "language",
          "repository", "shelfmark", "url", "rights", "record_id", "record_origin")


# --------------------------------------------------------------------------- parsers

def test_mods_parses_the_documented_subset():
    bib = B.parse_mods(MODS, "x.mods.xml")
    assert bib is not None
    assert bib.title == "Documentos históricos do Padroado do Oriente"
    assert bib.part_number == "IV"
    assert bib.creators == [B.Name(name="Silva, António", role="editor")]
    assert (bib.place, bib.publisher, bib.date_issued) == ("Lisboa", "Imprensa Nacional", "1947")
    assert (bib.repository, bib.shelfmark) == ("Biblioteca Nacional de Portugal", "BNP RES. 1234 V.")
    assert bib.language == "por"
    assert bib.record_id == "22"
    assert bib.record_origin == "human-supplied"
    assert bib.source_format == "mods"


def test_dc_parses_the_same_record():
    bib = B.parse_dc(json.dumps(DC), "x.dc.json")
    assert bib is not None
    assert bib.title == "Documentos históricos do Padroado do Oriente"
    assert bib.part_number == "IV"
    assert bib.creators == [B.Name(name="Silva, António", role="editor")]
    assert (bib.place, bib.publisher, bib.date_issued) == ("Lisboa", "Imprensa Nacional", "1947")
    assert (bib.repository, bib.shelfmark) == ("Biblioteca Nacional de Portugal", "BNP RES. 1234 V.")
    assert bib.record_id == "22"
    assert bib.source_format == "dc"


def test_the_two_formats_normalise_identically():
    """The property that makes 'support both' honest rather than aspirational."""
    m = B.parse_mods(MODS)
    d = B.parse_dc(json.dumps(DC))
    assert m is not None and d is not None
    for field in SHARED:
        assert getattr(m, field) == getattr(d, field), field
    assert m.creators == d.creators
    assert m.host is not None and d.host is not None
    assert (m.host.title, m.host.volume) == (d.host.title, d.host.volume)


def test_the_two_formats_render_the_same_citation():
    m = B.parse_mods(MODS)
    d = B.parse_dc(json.dumps(DC))
    kw = dict(doc_id=22, page_no=437, variant_label="edited: modern-portuguese@x", filename="doc22.pdf")
    assert B.format_citation(m, **kw) == B.format_citation(d, **kw)


def test_mods_with_namespace_prefix():
    """A `mods:` prefix must work as well as a default namespace."""
    prefixed = MODS.replace(
        '<mods xmlns="http://www.loc.gov/mods/v3"',
        '<mods:mods xmlns:mods="http://www.loc.gov/mods/v3"', 1,
    ).replace("<titleInfo>", "<mods:titleInfo>").replace("</titleInfo>", "</mods:titleInfo>")
    prefixed = prefixed.replace("<title>", "<mods:title>").replace("</title>", "</mods:title>")
    prefixed = prefixed.replace("<partNumber>", "<mods:partNumber>").replace("</partNumber>", "</mods:partNumber>")
    prefixed = prefixed.replace("</mods:mods>", "</mods:mods>").replace("</mods>", "</mods:mods>")
    prefixed = prefixed.replace("<mods:mods ", "<mods:mods ").replace("<mods:mods xmlns", "<mods:mods xmlns")
    bib = B.parse_mods(prefixed)
    assert bib is not None and bib.title == "Documentos históricos do Padroado do Oriente"


def test_mods_collection_wrapper_is_unwrapped():
    wrapped = f'<modsCollection xmlns="http://www.loc.gov/mods/v3">{MODS.split("?>",1)[1]}</modsCollection>'
    bib = B.parse_mods(wrapped)
    assert bib is not None and bib.title == "Documentos históricos do Padroado do Oriente"


def test_zotero_emits_copyrightdate_and_series():
    """Real Zotero exports use copyrightDate and relatedItem type="series"."""
    zotero = MODS.replace("<dateIssued encoding=\"w3cdtf\">1947</dateIssued>",
                          "<copyrightDate>1947</copyrightDate>")
    zotero = zotero.replace('<relatedItem type="host">', '<relatedItem type="series">')
    bib = B.parse_mods(zotero)
    assert bib is not None
    assert bib.date_issued == "1947"
    assert bib.host is not None and bib.host.volume == "4"


def test_top_level_part_carries_the_volume():
    """Where Zotero actually puts a volume-of-a-set designator."""
    zotero = MODS.replace(
        "<originInfo>", '<part><detail type="volume"><number>4." VOL. (1548- 1550)</number></detail></part><originInfo>'
    ).replace("<partNumber>IV</partNumber>", "")
    bib = B.parse_mods(zotero)
    assert bib is not None and bib.part_number == '4." VOL. (1548- 1550)'


def test_non_url_location_value_is_dropped():
    bib = B.parse_mods(MODS.replace(
        "<physicalLocation>Biblioteca Nacional de Portugal</physicalLocation>",
        "<url usage=\"primary display\">DHMPPO</url>"))
    assert bib is not None and bib.url is None
    bib2 = B.parse_mods(MODS.replace(
        "<physicalLocation>Biblioteca Nacional de Portugal</physicalLocation>",
        "<url>https://example.org/x</url>"))
    assert bib2 is not None and bib2.url == "https://example.org/x"


def test_note_with_citation_type_is_the_override():
    bib = B.parse_mods(MODS.replace(
        "<recordInfo>", '<note type="bibliographicCitation">Silva, Doc. Padroado, vol. IV, p. 437.</note><recordInfo>'))
    assert bib is not None and bib.citation_override == "Silva, Doc. Padroado, vol. IV, p. 437."


# --------------------------------------------------------------------------- failure modes

def test_malformed_xml_is_not_a_reference():
    assert B.parse_mods("<mods> not closed") is None


def test_malformed_json_is_not_a_reference():
    assert B.parse_dc("{not json") is None
    assert B.parse_dc('["a list"]') is None


def test_an_empty_record_is_not_a_reference():
    """A template/blank sidecar must not masquerade as a citation."""
    assert B.parse_mods('<mods xmlns="http://www.loc.gov/mods/v3"/>') is None
    assert B.parse_dc('{"@context": {}}') is None


def test_a_broken_sidecar_does_not_break_the_citation(tmp_path):
    doc = {"path": str(tmp_path / "d.pdf"), "kind": "pdf"}
    (tmp_path / "d.mods.xml").write_text("<mods>broken", encoding="utf-8")
    bib, warning = B.load_bibliography(doc)
    assert bib is None and warning is None
    assert B.format_citation(bib, doc_id=1, page_no=2, variant_label="raw", filename="d.pdf") \
        == "d.pdf — doc 1, p. 2 (raw)"


# --------------------------------------------------------------------------- lookup

def test_sidecar_paths_for_a_file_document(tmp_path):
    doc = {"path": str(tmp_path / "vol.pdf"), "kind": "pdf"}
    assert B.sidecar_path_for(doc, "dc") == tmp_path / "vol.dc.json"
    assert B.sidecar_path_for(doc, "mods") == tmp_path / "vol.mods.xml"
    assert B.sidecar_path_for(doc, "bib") == tmp_path / "vol.bib"


def test_sidecar_paths_for_a_directory_document(tmp_path):
    """A directory-of-images document keeps its sidecar INSIDE the folder."""
    folder = tmp_path / "vol04"
    folder.mkdir()
    doc = {"path": str(folder), "kind": "dir"}
    assert B.sidecar_path_for(doc, "dc") == folder / "vol04.dc.json"
    assert B.sidecar_path_for(doc, "mods") == folder / "vol04.mods.xml"


def test_no_sidecar_is_found_when_absent(tmp_path):
    doc = {"path": str(tmp_path / "vol.pdf"), "kind": "pdf"}
    assert B.find_sidecar(doc) == (None, None, None)


def test_two_sidecars_warns_and_prefers_the_one_a_human_edits(tmp_path):
    """JSON first: it is the format the owner maintains, so an edit to it must
    not be silently overridden by a stale MODS import."""
    doc = {"path": str(tmp_path / "vol.pdf"), "kind": "pdf"}
    (tmp_path / "vol.mods.xml").write_text(MODS, encoding="utf-8")
    (tmp_path / "vol.dc.json").write_text(json.dumps(DC), encoding="utf-8")
    path, fmt, warning = B.find_sidecar(doc)
    assert fmt == "dc" and path.name == "vol.dc.json"
    assert warning and "vol.dc.json" in warning and "vol.mods.xml" in warning


def test_json_beats_even_a_bib_import(tmp_path):
    doc = {"path": str(tmp_path / "vol.pdf"), "kind": "pdf"}
    (tmp_path / "vol.bib").write_text("@book{k, title={T}}", encoding="utf-8")
    (tmp_path / "vol.dc.json").write_text(json.dumps(DC), encoding="utf-8")
    path, fmt, warning = B.find_sidecar(doc)
    assert fmt == "dc" and warning and "vol.bib" in warning


def test_dc_sidecar_is_found_when_it_is_the_only_one(tmp_path):
    doc = {"path": str(tmp_path / "vol.pdf"), "kind": "pdf"}
    (tmp_path / "vol.dc.json").write_text(json.dumps(DC), encoding="utf-8")
    path, fmt, warning = B.find_sidecar(doc)
    assert fmt == "dc" and path.name == "vol.dc.json" and warning is None
    bib, _ = B.load_bibliography(doc)
    assert bib is not None and bib.title == DC["dcterms:title"]


# --------------------------------------------------------------------------- rendering

def test_no_sidecar_reproduces_the_pre_sidecar_citation_exactly():
    """The regression guard: absent a sidecar, `pha cite` output is unchanged."""
    for label in (None, "transcription: ocr@liteparse",
                  "edited: modern-portuguese@deepseek-v4-flash"):
        want = "vol01.pdf — doc 17, p. 3"
        if label:
            want += f" ({label})"
        assert B.format_citation(None, doc_id=17, page_no=3, variant_label=label,
                                 filename="vol01.pdf") == want


def test_clean_volume_normalises_real_zotero_designators():
    assert B.clean_volume("1.° VOL. (1499-1522)") == "1 (1499-1522)"
    assert B.clean_volume('4." VOL. (1548- 1550)') == "4 (1548-1550)"
    assert B.clean_volume("9.o VOL. (1562-1565)") == "9 (1562-1565)"
    assert B.clean_volume("7.° VOL. ( 1559)") == "7 (1559)"
    assert B.clean_volume("I") == "I"
    # unrecognisable values pass through rather than being mangled
    assert B.clean_volume("vol. 70, 72, 74") == "vol. 70, 72, 74"
    assert B.clean_volume(None) is None


def test_citation_appends_the_archive_locator_so_the_variant_survives():
    bib = B.parse_mods(MODS)
    text = B.format_citation(bib, doc_id=22, page_no=437,
                             variant_label="edited: modern-portuguese@x", filename="doc.pdf")
    assert text.endswith("— doc 22, p. 437 (edited: modern-portuguese@x)")
    assert "vol. IV" in text and "Imprensa Nacional" in text and "1947" in text


def test_citation_override_wins():
    bib = B.parse_mods(MODS)
    bib.citation_override = "Silva, Doc. Padroado, vol. IV."
    text = B.format_citation(bib, doc_id=22, page_no=437, variant_label="raw", filename="d.pdf")
    assert text == "Silva, Doc. Padroado, vol. IV. — doc 22, p. 437 (raw)"


def test_agent_drafted_references_are_marked_and_human_ones_are_not():
    machine = B.parse_mods(MODS.replace("human-supplied", "agent-drafted-unverified"))
    assert machine is not None and machine.is_unverified()
    text = B.format_citation(machine, doc_id=22, page_no=1, variant_label="raw", filename="d.pdf")
    assert text.endswith("[unverified reference]")

    human = B.parse_mods(MODS)
    assert human is not None and not human.is_unverified()
    assert "[unverified reference]" not in B.format_citation(
        human, doc_id=22, page_no=1, variant_label="raw", filename="d.pdf")

    # an absent origin is not nagged about
    silent = B.parse_mods(MODS.replace("<recordOrigin>human-supplied</recordOrigin>", ""))
    assert silent is not None and not silent.is_unverified()


def test_the_zotero_origin_marker_counts_as_unverified():
    """The 24 sample sidecars use this value: un-reviewed, but not badged."""
    bib = B.parse_mods(MODS.replace("human-supplied", "fetched-from-zotero-unverified"))
    assert bib is not None and bib.is_unverified()
    assert bib.is_agent_drafted() is False


def test_a_human_marker_wins_over_where_the_record_came_from():
    """A human-curated import must not be flagged forever for saying 'zotero'."""
    from personal_historical_archive.bibliography import verified_from_origin

    assert verified_from_origin("human-confirmed (corrected in Zotero)") is True
    assert verified_from_origin("fetched-from-zotero-unverified") is False
    assert verified_from_origin("agent-drafted-unverified") is False
    assert verified_from_origin("human-supplied") is True
    # nothing to judge: no record, or an unrecognised value
    assert verified_from_origin(None) is None
    assert verified_from_origin("") is None
    # an import is un-reviewed even without the word "unverified"
    assert verified_from_origin("imported") is False


def test_only_a_model_drafted_reference_is_badged_in_the_citation():
    """Being un-reviewed and being a model's guess are different things.

    An invented shelfmark must be marked wherever it is shown; a record
    imported from the owner's own library is their data, reported by `pha bib`
    rather than warned about in every footnote.
    """
    from personal_historical_archive.bibliography import (
        agent_drafted_from_origin as badged,
    )

    assert badged("agent-drafted-unverified") is True
    assert badged("machine-drafted") is True
    assert badged("fetched-from-zotero-unverified") is False
    assert badged("imported") is False
    assert badged("human-supplied") is False
    assert badged("human-confirmed (Zotero)") is False
    assert badged(None) is False

    zotero = B.parse_mods(MODS.replace("human-supplied", "fetched-from-zotero-unverified"))
    assert zotero is not None
    text = B.format_citation(zotero, doc_id=22, page_no=1, variant_label="raw", filename="d.pdf")
    assert "[unverified reference]" not in text


def test_is_unverified_uses_the_shared_rule():
    from personal_historical_archive.bibliography import Bibliography

    assert Bibliography(record_origin="agent-drafted-unverified").is_unverified() is True
    assert Bibliography(record_origin="human-supplied").is_unverified() is False
    assert Bibliography(record_origin="human-confirmed (Zotero)").is_unverified() is False
    assert Bibliography(record_origin=None).is_unverified() is False


# --------------------------------------------------- editable JSON (round-trip)

def test_a_hand_written_json_sidecar_needs_no_vocabulary_ceremony():
    """The format a person writes: plain keys, no JSON-LD prefixes."""
    bib = B.parse_dc(json.dumps({
        "title": "Documentos del Japon 1547-1557",
        "part_number": "II",
        "creators": [{"name": "Juan Ruiz-de-Medina", "role": "author"}],
        "place_of_publication": "Roma",
        "publisher": "Instituto Histórico de la Compañia de Jesús",
        "date_issued": "1990",
        "shelfmark": "BNP RES. 1234 V.",
        "is_part_of": {"title": "Monumenta Historica Societatis IESU", "volume_number": "137"},
        "record_origin": "human-supplied",
        "citation": "custom override",
    }))
    assert bib is not None
    assert bib.title == "Documentos del Japon 1547-1557"
    assert bib.part_number == "II"
    assert bib.creators == [B.Name(name="Juan Ruiz-de-Medina", role="author")]
    assert (bib.place, bib.publisher, bib.date_issued) == ("Roma", "Instituto Histórico de la Compañia de Jesús", "1990")
    assert bib.shelfmark == "BNP RES. 1234 V."
    assert bib.host is not None and (bib.host.title, bib.host.volume) == ("Monumenta Historica Societatis IESU", "137")
    assert bib.citation_override == "custom override"
    assert not bib.is_unverified()


def test_key_spelling_is_forgiving():
    """`partNumber`, `part_number` and `pha:partNumber` are the same field."""
    for key in ("part_number", "partNumber", "pha:partNumber", "dcterms:partNumber"):
        bib = B.parse_dc(json.dumps({"title": "T", key: "IV"}))
        assert bib is not None and bib.part_number == "IV", key


def test_mods_round_trips_through_the_editable_json():
    """The conversion that lets a Zotero MODS import be edited as JSON."""
    mods = B.parse_mods(MODS)
    assert mods is not None
    for qualified in (False, True):
        back = B.parse_dc(B.to_dc_json_text(mods, qualified=qualified))
        assert back is not None, qualified
        for field in SHARED:
            assert getattr(back, field) == getattr(mods, field), (qualified, field)
        assert back.creators == mods.creators
        assert back.identifiers == mods.identifiers
        assert back.subjects == mods.subjects
        assert back.host is not None and mods.host is not None
        assert (back.host.title, back.host.volume) == (mods.host.title, mods.host.volume)


def test_the_round_trip_renders_the_same_citation():
    mods = B.parse_mods(MODS)
    back = B.parse_dc(B.to_dc_json_text(mods))
    kw = dict(doc_id=22, page_no=437, variant_label="edited: x@y", filename="d.pdf")
    assert B.format_citation(back, **kw) == B.format_citation(mods, **kw)


def test_to_dc_json_omits_empty_fields():
    bib = B.parse_dc(json.dumps({"title": "Only a title"}))
    out = B.to_dc_json(bib)
    assert out == {"title": "Only a title"}


def test_qualified_emission_is_json_ld():
    bib = B.parse_mods(MODS)
    out = B.to_dc_json(bib, qualified=True)
    assert out["@context"]["dcterms"] == "http://purl.org/dc/terms/"
    assert out["dcterms:title"] == bib.title
    assert "pha:shelfmark" in out


# --------------------------------------------------------------- BibTeX

BIBTEX = r'''
@string{agnc = {Agência Geral das Colónias}}

@book{rego1950,
  title     = {Documenta{\c{c}}{\~a}o para a hist{\'o}ria das miss{\~o}es do padroado portugu{\^e}s do Oriente},
  volume    = {4},
  editor    = {Rego, Ant{\'o}nio da Silva},
  publisher = agnc,
  address   = {Lisboa},
  year      = {1950},
  pages     = {599},
  language  = {Portuguese},
  isbn      = {972-95100-7-5},
  shelfmark = {BNP RES. 1234 V.},
  record_origin = {human-supplied}
}
'''


def test_bibtex_parses_accents_macros_and_nonstandard_fields():
    """BibTeX is readable back: LaTeX accents, @string macros, our own fields."""
    bib = B.parse_bib(BIBTEX)
    assert bib is not None
    assert bib.title == ("Documentação para a história das missões do padroado "
                         "português do Oriente")
    assert bib.part_number == "4"
    assert bib.creators == [B.Name(name="Rego, António da Silva", role="editor")]
    assert (bib.place, bib.publisher, bib.date_issued) == ("Lisboa", "Agência Geral das Colónias", "1950")
    assert bib.extent == "599 p."
    assert [(i.type, i.value) for i in bib.identifiers] == [("isbn", "972-95100-7-5")]
    assert bib.shelfmark == "BNP RES. 1234 V."
    assert bib.source_format == "bib"
    assert not bib.is_unverified() and not bib.is_agent_drafted()


def test_bibtex_page_range_versus_page_count():
    article = B.parse_bib("@article{a, title={T}, journal={R}, pages={311--338}}")
    assert article is not None
    assert article.extent == "pp. 311–338"
    assert article.host is not None and article.host.title == "R"
    book = B.parse_bib("@book{b, title={T}, pages={599}}")
    assert book is not None and book.extent == "599 p."


def test_bibtex_series_becomes_the_containing_work():
    bib = B.parse_bib(
        '@book{k, title={Documentos del Japon}, volume={II}, '
        'series={Monumenta Historica Societatis IESU}, number={137}, year={1990}}')
    assert bib is not None
    assert (bib.host.title, bib.host.volume) == ("Monumenta Historica Societatis IESU", "137")


def test_bibtex_crossref_inherits_the_parent_entry():
    bib = B.parse_bib(
        '@book{parent, title={The Volume}, publisher={P}, address={Roma}, year={1990}}\n'
        '@inbook{child, author={Silva, Joao}, title={A Chapter}, crossref={parent}}')
    assert bib is not None                       # the first entry is the one used
    assert bib.title == "The Volume"
    # a crossref child, when it is first, inherits the parent's imprint
    child_first = B.parse_bib(
        '@inbook{child, author={Silva, Joao}, title={A Chapter}, crossref={parent}}\n'
        '@book{parent, title={The Volume}, publisher={P}, address={Roma}, year={1990}}')
    assert child_first is not None
    assert child_first.publisher == "P" and child_first.date_issued == "1990"
    assert child_first.host is not None and child_first.host.title == "The Volume"


def test_bibtex_without_provenance_is_imported_not_badged():
    """A pasted entry came from somewhere; it is reported, not warned about."""
    bib = B.parse_bib("@book{k, title={T}}")
    assert bib is not None
    assert bib.record_origin == "imported"
    assert bib.is_unverified() and not bib.is_agent_drafted()


def test_bibtex_skips_and_others():
    bib = B.parse_bib("@book{k, title={T}, author={Silva, Joao and others}}")
    assert bib is not None
    assert [n.name for n in bib.creators] == ["Silva, Joao"]


def test_bibtex_entry_count_and_multiple_entries_use_the_first():
    assert B.bib_entry_count(BIBTEX) == 1
    assert B.bib_entry_count(BIBTEX + "@book{two, title={Second}}") == 2


def test_bibtex_round_trips_through_the_emitter():
    """Emit -> read back must preserve the fields we care about."""
    original = B.parse_mods(MODS)
    assert original is not None
    text = B.to_bibtex(original)
    back = B.parse_bib(text)
    assert back is not None
    assert back.title == original.title
    assert back.part_number == original.part_number
    assert back.publisher == original.publisher
    assert back.date_issued == original.date_issued
    assert back.shelfmark == original.shelfmark
    assert back.repository == original.repository
    assert back.language == original.language
    assert [(i.type, i.value) for i in back.identifiers] == \
           [(i.type, i.value) for i in original.identifiers]
    assert [(n.name, n.role) for n in back.creators] == \
           [(n.name, n.role) for n in original.creators]


def test_bibtex_emitter_escapes_markup_and_keeps_accents_readable():
    bib = B.parse_dc(json.dumps({"title": "Rights & duties_100% {notes}",
                                 "publisher": "Ação & Cia"}))
    text = B.to_bibtex(bib)
    assert r"Rights \& duties\_100\% \{notes\}" in text
    assert "Ação" in text, "accents stay UTF-8, readable and editable"
    back = B.parse_bib(text)
    assert back is not None and back.title == "Rights & duties_100% {notes}"


def test_bibtex_key_is_derived_and_ascii():
    bib = B.parse_dc(json.dumps({"title": "Documentação para a história",
                                 "creators": [{"name": "Rego, António da Silva"}],
                                 "date_issued": "1950"}))
    assert B.bibtex_key(bib) == "Rego1950Documentacao"


def test_bibtex_emitter_can_stamp_an_agent_draft():
    bib = B.parse_mods(MODS)
    text = B.to_bibtex(bib, origin="agent-drafted-unverified")
    assert "record_origin = {agent-drafted-unverified}" in text
    back = B.parse_bib(text)
    assert back is not None and back.is_agent_drafted()
