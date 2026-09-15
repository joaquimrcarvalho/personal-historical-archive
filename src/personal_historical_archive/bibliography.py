"""Per-document bibliographic references.

A document may carry a bibliographic reference in a **sidecar** beside it:

    <stem>.mods.xml   MODS 3.8 (Library of Congress)
    <stem>.dc.json    qualified Dublin Core (DCMI Metadata Terms) as JSON-LD

The rule is deliberately dumb: **if the sidecar exists we have a reference, if
not we do not.** There is no inheritance from a parent directory and no
default. That is the opposite of the pipeline sidecars in ``sidecar.py``, and
on purpose: a pipeline default is safe to inherit, but an inherited
*bibliographic* default produces a confident wrong citation, which is worse
than no citation at all.

Both formats parse into one :class:`Bibliography` record, and one
:func:`format_citation` renders it — so `pha cite`, the page JSON, the library
front matter, the served viewer and MCP cannot drift apart. Adding a third
format later means one parser, not a new rendering path.

See BIBLIOGRAPHY_PLAN.md for the design and the mapping table.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODS_SUFFIX = ".mods.xml"
DC_SUFFIX = ".dc.json"
BIB_SUFFIX = ".bib"
#: Precedence when a document somehow has more than one. JSON first: it is the
#: format a human edits, so it must not be overridden by a stale import.
FORMAT_PRECEDENCE = ("dc", "mods", "bib")
_SUFFIX_BY_FORMAT = {"dc": DC_SUFFIX, "mods": MODS_SUFFIX, "bib": BIB_SUFFIX}
SIDECAR_SUFFIXES = tuple(_SUFFIX_BY_FORMAT[f] for f in FORMAT_PRECEDENCE)

#: A reference is only treated as verified when the record says a human
#: supplied (or confirmed) it.
_VERIFIED_MARKERS = ("human-supplied", "human-confirmed", "human-reviewed")
#: A reference a MODEL produced. An invented shelfmark, volume or imprint reads
#: exactly like a correct one, so this is the kind that must be marked wherever
#: it is shown as a citation.
_AGENT_MARKERS = ("agent", "machine-drafted", "model-drafted", "draft")
#: Imported from a source the owner maintains (their Zotero library). Worth
#: reporting as not-yet-reviewed, but it is the owner's own data rather than a
#: model's guess, so it does not clutter every footnote with a warning.
_IMPORT_MARKERS = ("zotero", "imported", "unverified")


def verified_from_origin(origin: str | None) -> bool | None:
    """Whether a reference counts as human-verified, given its recorded provenance.

    The single home for the *review status*: `pha bib`, `pha serve` and the MCP
    tools all call it, so a reference cannot be reported as reviewed in one
    place and not another.

    Returns ``None`` when there is nothing to judge (no record, or an
    unrecognised provenance value — not reported, rather than nagged about).
    A human marker WINS over a mention of where the record came from, so
    ``"human-confirmed (corrected in Zotero)"`` is verified; otherwise a
    human-curated Zotero import would be flagged forever for saying "zotero".
    """
    text = (origin or "").strip().lower()
    if not text:
        return None
    if any(v in text for v in _VERIFIED_MARKERS):
        return True
    if any(u in text for u in _AGENT_MARKERS + _IMPORT_MARKERS):
        return False
    return None


def agent_drafted_from_origin(origin: str | None) -> bool:
    """Whether a citation should carry the inline unverified warning.

    Deliberately narrower than :func:`verified_from_origin`: only a
    model-produced reference is badged in the citation text. A record imported
    from the owner's own curated library is their data, not a model's
    confabulation, so `pha bib` reports it while a footnote stays clean.
    """
    text = (origin or "").strip().lower()
    if any(v in text for v in _VERIFIED_MARKERS):
        return False
    return any(u in text for u in _AGENT_MARKERS)


# --------------------------------------------------------------------------- record


@dataclass
class Name:
    """A person or body, with the role it played in the work."""
    name: str
    role: str | None = None


@dataclass
class Identifier:
    value: str
    type: str | None = None


@dataclass
class Host:
    """The containing work (a multi-volume set, a series, a periodical)."""
    title: str | None = None
    volume: str | None = None
    date: str | None = None
    publisher: str | None = None
    identifiers: list[Identifier] = field(default_factory=list)
    shelfmark: str | None = None


@dataclass
class Bibliography:
    """One document's reference, normalised across both sidecar formats."""
    title: str | None = None
    sub_title: str | None = None
    part_number: str | None = None
    part_name: str | None = None
    creators: list[Name] = field(default_factory=list)
    type: str | None = None
    genre: str | None = None
    place: str | None = None
    publisher: str | None = None
    date_issued: str | None = None
    edition: str | None = None
    language: str | None = None
    form: str | None = None
    extent: str | None = None
    identifiers: list[Identifier] = field(default_factory=list)
    repository: str | None = None
    shelfmark: str | None = None
    url: str | None = None
    host: Host | None = None
    rights: str | None = None
    subjects: list[str] = field(default_factory=list)
    classification: str | None = None
    citation_override: str | None = None
    record_id: str | None = None
    record_origin: str | None = None
    source_format: str = ""
    source_path: str = ""

    def has_content(self) -> bool:
        """True when the record actually says something citable.

        A sidecar that parses but names nothing is treated as *no reference*,
        so an empty or template file cannot masquerade as a citation.
        """
        return bool(
            self.citation_override or self.title or self.creators
            or self.publisher or self.date_issued or self.shelfmark
            or self.repository or self.identifiers
        )

    def is_unverified(self) -> bool:
        """True when the record is not human-verified (drives `pha bib`).

        Covers both a model-drafted reference and one imported from a source
        the owner has not yet reviewed — see :meth:`is_agent_drafted` for the
        narrower claim that a warning belongs *in the citation itself*.
        """
        return verified_from_origin(self.record_origin) is False

    def is_agent_drafted(self) -> bool:
        """True when a model produced the record, so a citation must say so."""
        return agent_drafted_from_origin(self.record_origin)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- lookup


def doc_layer(doc) -> tuple[Path, str]:
    """The directory a document's sidecars live in, and its stem.

    The rule: ``<stem>.<suffix>`` in the document's own directory layer. For a
    directory-of-images document (``kind == "dir"``) that layer is the folder
    itself, so a sidecar placed inside travels with the folder.
    """
    p = Path(doc["path"])
    if p.is_dir():
        return p, p.name
    return p.parent, p.stem


def sidecar_path_for(doc, fmt: str) -> Path:
    """The path of one format's sidecar, whether or not it exists."""
    base, stem = doc_layer(doc)
    return base / f"{stem}{_SUFFIX_BY_FORMAT[fmt]}"


def candidate_sidecars(doc) -> list[tuple[str, Path]]:
    """Every candidate sidecar for a document, most authoritative first.

    ``.dc.json`` comes first because JSON is the format a human edits: when two
    sidecars disagree, the one the owner maintains must win, or their edits
    would be silently ignored. The others are import formats.
    """
    return [(fmt, sidecar_path_for(doc, fmt)) for fmt in FORMAT_PRECEDENCE]


def find_sidecar(doc) -> tuple[Path | None, str | None, str | None]:
    """Locate a document's sidecar.

    Returns ``(path, fmt, warning)``. When more than one exists the most
    authoritative wins (see :func:`candidate_sidecars`) but a warning is
    returned: precedence between records that may disagree must never be silent.
    """
    present = [(fmt, path) for fmt, path in candidate_sidecars(doc) if path.is_file()]
    if not present:
        return None, None, None
    fmt, path = present[0]
    if len(present) > 1:
        others = ", ".join(p.name for _f, p in present[1:])
        return path, fmt, (
            f"more than one bibliographic sidecar for this document; using "
            f"{path.name} and ignoring {others}")
    return path, fmt, None


def sidecar_sha(path: Path) -> str | None:
    """Content hash of a sidecar, so the stored copy is refreshed only on change."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def load_bibliography(doc) -> tuple[Bibliography | None, str | None]:
    """Read a document's reference from its sidecar.

    Returns ``(record, warning)``. A missing sidecar is ``(None, None)`` — a
    normal state, not an error. A malformed one is ``(None, warning)``: a
    broken sidecar must never break `pha cite`.
    """
    path, fmt, warning = find_sidecar(doc)
    if path is None:
        return None, warning
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"cannot read {path.name}: {e}"
    bib = parse_sidecar(text, fmt, str(path))
    if fmt == "bib":
        # A sidecar describes ONE document, so several entries is ambiguous —
        # say so rather than silently using the first.
        extra = bib_entry_count(text) - 1
        if extra > 0:
            note = (f"{path.name} has {extra + 1} BibTeX entries; using the first "
                    f"and ignoring {extra}")
            warning = f"{warning}; {note}" if warning else note
    return bib, warning


# --------------------------------------------------------------------------- MODS


def _local(tag: str) -> str:
    """ElementTree tag -> local name, so a prefix or default ns both work."""
    return tag.rsplit("}", 1)[-1]


def _children(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name]


def _child(el, name: str):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, name: str) -> str | None:
    c = _child(el, name)
    if c is None:
        return None
    return (c.text or "").strip() or None


def _name_from_mods(n) -> Name | None:
    parts = _children(n, "namePart")
    family = next((p for p in parts if (p.get("type") or "") == "family"), None)
    given = next((p for p in parts if (p.get("type") or "") == "given"), None)
    if family is not None and (family.text or "").strip():
        fam = (family.text or "").strip()
        giv = (given.text or "").strip() if given is not None else ""
        name = f"{fam}, {giv}" if giv else fam
    else:
        name = " ".join((p.text or "").strip() for p in parts if (p.text or "").strip())
    if not name:
        return None
    role = None
    r = _child(n, "role")
    if r is not None:
        terms = _children(r, "roleTerm")
        t = next((x for x in terms if (x.get("type") or "") == "text"), None)
        t = t if t is not None else (terms[0] if terms else None)
        if t is not None and (t.text or "").strip():
            role = t.text.strip()
    return Name(name=name, role=role)


def _host_from_related(ri) -> Host:
    h = Host()
    ti = _child(ri, "titleInfo")
    if ti is not None:
        h.title = _text(ti, "title") or _text(ti, "partName")
    part = _child(ri, "part")
    if part is not None:
        for det in _children(part, "detail"):
            if (det.get("type") or "").lower() in ("volume", "part", "issue"):
                h.volume = _text(det, "number") or h.volume
    oi = _child(ri, "originInfo")
    if oi is not None:
        h.publisher = _text(oi, "publisher")
        h.date = _text(oi, "dateIssued") or _text(oi, "copyrightDate")
    for i in _children(ri, "identifier"):
        if (i.text or "").strip():
            h.identifiers.append(Identifier(value=i.text.strip(), type=i.get("type")))
    loc = _child(ri, "location")
    if loc is not None:
        h.shelfmark = _text(loc, "shelfLocator") or _text(loc, "physicalLocation")
    return h


def parse_mods(text: str, source_path: str = "") -> Bibliography | None:
    """Parse a MODS record (bare ``<mods>`` or a ``<modsCollection>`` wrapper).

    Matching is on local names, and unknown elements are ignored, so a fuller
    library export still works. Returns None when the XML is unusable.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    mods = root
    if _local(root.tag) == "modsCollection":
        inner = _children(root, "mods")
        if not inner:
            return None
        mods = inner[0]
    if _local(mods.tag) != "mods":
        return None

    bib = Bibliography(source_format="mods", source_path=source_path)

    title_infos = _children(mods, "titleInfo")
    main = next((t for t in title_infos if not (t.get("type") or "")), None)
    main = main if main is not None else (title_infos[0] if title_infos else None)
    if main is not None:
        bib.title = _text(main, "title")
        bib.sub_title = _text(main, "subTitle")
        bib.part_number = _text(main, "partNumber")
        bib.part_name = _text(main, "partName")

    # the volume of the work itself: titleInfo/partNumber, else a top-level
    # `part/detail` (which is where Zotero puts it for a volume of a set)
    if not bib.part_number:
        part = _child(mods, "part")
        if part is not None:
            for det in _children(part, "detail"):
                if (det.get("type") or "").lower() in ("volume", "part", "issue"):
                    bib.part_number = _text(det, "number")
                    if bib.part_number:
                        break

    for n in _children(mods, "name"):
        person = _name_from_mods(n)
        if person is not None:
            bib.creators.append(person)

    bib.type = _text(mods, "typeOfResource")
    for g in _children(mods, "genre"):
        if (g.text or "").strip():
            bib.genre = g.text.strip()
            break

    oi = _child(mods, "originInfo")
    if oi is not None:
        place = _child(oi, "place")
        if place is not None:
            pt = _child(place, "placeTerm")
            if pt is not None and (pt.text or "").strip():
                bib.place = pt.text.strip()
        bib.publisher = _text(oi, "publisher")
        # Zotero emits copyrightDate for books; both mean "the imprint date"
        bib.date_issued = (_text(oi, "dateIssued") or _text(oi, "copyrightDate")
                           or _text(oi, "dateCreated"))
        bib.edition = _text(oi, "edition")

    lang = _child(mods, "language")
    if lang is not None:
        lt = _child(lang, "languageTerm")
        if lt is not None and (lt.text or "").strip():
            bib.language = lt.text.strip()

    pd = _child(mods, "physicalDescription")
    if pd is not None:
        bib.form = _text(pd, "form")
        bib.extent = _text(pd, "extent")

    for i in _children(mods, "identifier"):
        if (i.text or "").strip():
            bib.identifiers.append(Identifier(value=i.text.strip(), type=i.get("type")))

    loc = _child(mods, "location")
    if loc is not None:
        bib.repository = _text(loc, "physicalLocation")
        bib.shelfmark = _text(loc, "shelfLocator")
        url = _text(loc, "url")
        # Zotero's URL field is sometimes a shorthand, not a URL
        if url and re.match(r"^https?://", url):
            bib.url = url

    bib.rights = _text(mods, "accessCondition")
    bib.classification = _text(mods, "classification")
    for s in _children(mods, "subject"):
        topic = _text(s, "topic") or _text(s, "geographic") or _text(s, "name")
        if topic:
            bib.subjects.append(topic)

    for n in _children(mods, "note"):
        if (n.get("type") or "").strip().lower() in ("bibliographiccitation", "citation"):
            bib.citation_override = ((n.text or "").strip()) or None

    # the containing work: MODS says `host`, Zotero's export says `series`
    for ri in _children(mods, "relatedItem"):
        if (ri.get("type") or "").lower() in ("host", "series") and bib.host is None:
            bib.host = _host_from_related(ri)

    ri = _child(mods, "recordInfo")
    if ri is not None:
        ids = _children(ri, "recordIdentifier")
        pha = next((x for x in ids if (x.get("source") or "").lower() == "pha"), None)
        chosen = pha if pha is not None else (ids[0] if ids else None)
        if chosen is not None and (chosen.text or "").strip():
            bib.record_id = chosen.text.strip()
        bib.record_origin = _text(ri, "recordOrigin")

    return bib if bib.has_content() else None


# --------------------------------------------------------------------------- Dublin Core (JSON-LD)


def _canon(key: str) -> str:
    """Canonical field name: drop any prefix, then every non-alphanumeric.

    So ``dcterms:isPartOf``, ``pha:is_part_of``, ``isPartOf`` and
    ``is_part_of`` are one and the same field. That lets a hand-written JSON
    sidecar use plain, readable keys (``part_number``, ``shelfmark``,
    ``record_origin``) with no vocabulary ceremony, while a fully qualified
    JSON-LD record from another tool still parses unchanged.
    """
    return re.sub(r"[^a-z0-9]", "", key.split(":")[-1].lower())


def _dc_index(data: dict) -> dict:
    """Map a JSON sidecar's keys to canonical field names (see :func:`_canon`)."""
    out: dict = {}
    for k, v in data.items():
        if k == "@context":
            continue
        out.setdefault(_canon(k), v)
    return out


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _scalar(v) -> str | None:
    """First usable string out of a JSON-LD value, list or nested node."""
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        for item in v:
            got = _scalar(item)
            if got:
                return got
        return None
    if isinstance(v, dict):
        for key in ("@value", "@id", "value", "name", "title"):
            if key in v:
                got = _scalar(v[key])
                if got:
                    return got
        return None
    return None


def _node_get(node: dict, *names: str) -> str | None:
    """Look up any of `names` on a nested node, by canonical field name."""
    if not isinstance(node, dict):
        return None
    idx = _dc_index(node)
    for n in names:
        key = _canon(n)
        if key in idx:
            got = _scalar(idx[key])
            if got:
                return got
    return None


def parse_dc(text: str, source_path: str = "") -> Bibliography | None:
    """Parse a JSON bibliographic sidecar.

    Deliberately forgiving about key spelling, because this is the format a
    person writes by hand: ``title``/``dcterms:title``, ``part_number``/
    ``partNumber``/``pha:partNumber`` and ``shelfmark``/``pha:shelfmark`` all
    work (see :func:`_canon`). A fully qualified JSON-LD record from another
    tool therefore parses unchanged, and so does a plain JSON file with
    readable keys.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    idx = _dc_index(data)
    bib = Bibliography(source_format="dc", source_path=source_path)

    bib.title = _scalar(idx.get("title"))
    bib.sub_title = _scalar(idx.get("alternative") or idx.get("subtitle"))
    bib.part_number = _scalar(idx.get("partnumber") or idx.get("volume"))
    bib.part_name = _scalar(idx.get("partname"))

    creator_values = (idx.get("creators") or idx.get("creator") or idx.get("authors")
                      or idx.get("contributor"))
    for c in _as_list(creator_values):
        if isinstance(c, dict):
            nm = _node_get(c, "name", "creator", "contributor")
            role = _node_get(c, "role")
            if nm:
                bib.creators.append(Name(name=nm, role=role))
        else:
            nm = _scalar(c)
            if nm:
                bib.creators.append(Name(name=nm))

    bib.type = _scalar(idx.get("type"))
    bib.genre = _scalar(idx.get("genre"))
    bib.place = _scalar(idx.get("placeofpublication") or idx.get("place"))
    bib.publisher = _scalar(idx.get("publisher"))
    bib.date_issued = (_scalar(idx.get("issued")) or _scalar(idx.get("dateissued"))
                       or _scalar(idx.get("date")))
    bib.edition = _scalar(idx.get("edition"))
    bib.language = _scalar(idx.get("language"))
    bib.form = _scalar(idx.get("format"))
    bib.extent = _scalar(idx.get("extent"))

    for i in _as_list(idx.get("identifier") or idx.get("identifiers")):
        if isinstance(i, dict):
            val = _scalar(i.get("@id") or i.get("@value") or i.get("value"))
            typ = _node_get(i, "type")
            if val:
                bib.identifiers.append(Identifier(value=val, type=typ))
        else:
            val = _scalar(i)
            if val:
                bib.identifiers.append(Identifier(value=val))

    bib.repository = _scalar(idx.get("repository"))
    bib.shelfmark = _scalar(idx.get("shelfmark"))
    bib.url = _scalar(idx.get("url"))
    bib.rights = _scalar(idx.get("rights"))
    bib.classification = _scalar(idx.get("classification"))
    bib.citation_override = _scalar(idx.get("bibliographiccitation") or idx.get("citation"))
    bib.record_origin = _scalar(idx.get("recordorigin"))
    bib.record_id = _scalar(idx.get("recordid")) or _scalar(data.get("@id"))
    for s in _as_list(idx.get("subject") or idx.get("subjects")):
        val = _scalar(s)
        if val:
            bib.subjects.append(val)

    # the containing work: `dcterms:isPartOf` may be a string or a nested node
    ip = idx.get("ispartof")
    if ip is not None:
        if isinstance(ip, dict):
            host_idx = _dc_index(ip)
            h = Host(
                title=_node_get(ip, "title"),
                volume=_node_get(ip, "volumenumber", "volume", "partnumber"),
                date=_node_get(ip, "dateissued", "issued", "date"),
                publisher=_node_get(ip, "publisher"),
                shelfmark=_node_get(ip, "shelfmark"),
            )
            for i in _as_list(host_idx.get("identifier") or host_idx.get("identifiers")):
                if isinstance(i, dict):
                    val = _scalar(i.get("@id") or i.get("@value") or i.get("value"))
                    typ = _node_get(i, "type")
                else:
                    val, typ = _scalar(i), None
                if val:
                    h.identifiers.append(Identifier(value=val, type=typ))
            bib.host = h
        else:
            title = _scalar(ip)
            if title:
                bib.host = Host(title=title)

    return bib if bib.has_content() else None


def parse_sidecar(text: str, fmt: str, source_path: str = "") -> Bibliography | None:
    """Parse by explicit format — ``"mods"``, ``"dc"`` or ``"bib"``."""
    if fmt == "mods":
        return parse_mods(text, source_path)
    if fmt == "bib":
        return parse_bib(text, source_path)
    return parse_dc(text, source_path)


# --------------------------------------------------------------------------- BibTeX


#: Combining marks LaTeX accent commands map to. NFC composition turns e.g.
#: ``\'e`` into ``é`` and ``\c{c}`` into ``ç``.
_LATEX_ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
    "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b",
    "c": "\u0327", "k": "\u0328", "r": "\u030a", "d": "\u0323", "b": "\u0331",
}
_LATEX_WRAPPERS = re.compile(
    r"\\(?:emph|textit|textbf|textsc|texttt|textrm|textsf|textmd|textnormal|mbox)"
    r"\s*\{([^{}]*)\}")
_LATEX_ACCENT_RE = re.compile(r"\\(['`^\"~=.\-uvHckrdb])\s*\{?([A-Za-z])\}?")

_BIB_GENRE = {
    "book": "book", "booklet": "book", "inbook": "chapter", "incollection": "chapter",
    "inproceedings": "article", "conference": "article", "article": "article",
    "phdthesis": "thesis", "mastersthesis": "thesis", "techreport": "report",
    "manual": "manual", "misc": "misc", "unpublished": "unpublished",
    "manuscript": "manuscript", "proceedings": "book",
}


def _bib_unescape(value: str) -> str:
    """BibTeX value text -> plain text.

    Strips the brace groups BibTeX uses to protect capitalisation
    (``{S}ocietatis`` -> ``Societatis``) and translates the LaTeX accent
    commands that show up in Portuguese, Spanish, French and Latin titles
    (``{\\'e}`` -> ``é``, ``\\c{c}`` -> ``ç``). This is the one genuinely fiddly
    part of reading BibTeX, and it is why BibTeX is an *input* format here
    rather than the storage format.
    """
    if not value:
        return ""
    s = _LATEX_WRAPPERS.sub(r"\1", value)

    def accent(m: re.Match) -> str:
        mark = _LATEX_ACCENTS.get(m.group(1))
        char = m.group(2)
        return unicodedata.normalize("NFC", char + mark) if mark else char

    s = _LATEX_ACCENT_RE.sub(accent, s)
    # Protect *escaped* braces from the brace-stripping below, so a literal
    # `{` in a title survives the round trip.
    s = s.replace(r"\{", "\x00OPEN\x00").replace(r"\}", "\x00CLOSE\x00")
    for old, new in ((r"\&", "&"), (r"\%", "%"), (r"\$", "$"), (r"\#", "#"),
                     (r"\_", "_"), ("---", "—"), ("--", "–")):
        s = s.replace(old, new)
    s = s.replace("~", " ")
    s = re.sub(r"\\[A-Za-z]+\s*", "", s)          # drop any leftover command
    s = s.replace("{", "").replace("}", "")
    s = s.replace("\x00OPEN\x00", "{").replace("\x00CLOSE\x00", "}")
    return re.sub(r"\s+", " ", s).strip()


def _split_top(text: str, sep: str) -> list[str]:
    """Split on `sep` at brace depth 0, outside double quotes."""
    parts: list[str] = []
    depth = 0
    quoted = False
    current: list[str] = []
    for ch in text:
        if ch == '"' and depth == 0:
            quoted = not quoted
        elif ch == "{" and not quoted:
            depth += 1
        elif ch == "}" and not quoted:
            depth = max(0, depth - 1)
        if ch == sep and depth == 0 and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def _bib_entries(text: str) -> list[tuple[str, str, str]]:
    """Split a .bib into ``(type, key, body)``; `key` is "" for @string etc."""
    entries: list[tuple[str, str, str]] = []
    i = 0
    while True:
        at = text.find("@", i)
        if at < 0:
            break
        m = re.match(r"@([A-Za-z]+)\s*[{(]", text[at:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        start = at + m.end()
        opener = text[at + m.end() - 1]
        closer = "}" if opener == "{" else ")"
        depth = 1
        j = start
        while j < len(text) and depth:
            if text[j] == opener:
                depth += 1
            elif text[j] == closer:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[start:j]
        i = j + 1
        if etype in ("comment", "preamble"):
            continue
        if etype == "string":
            entries.append((etype, "", body))
            continue
        fields = _split_top(body, ",")
        key = fields[0].strip() if fields else ""
        entries.append((etype, key, ",".join(fields[1:]) if len(fields) > 1 else ""))
    return entries


def _bib_fields(body: str) -> dict:
    """``name = value`` pairs of an entry body, unescaped and macro-expanded.

    Field names are canonicalised the same way as JSON keys (:func:`_canon`),
    so ``record_origin``, ``recordOrigin`` and Zotero's non-standard spellings
    all reach the same lookup.
    """
    out: dict = {}
    for pair in _split_top(body, ","):
        if "=" not in pair:
            continue
        name, _, raw = pair.partition("=")
        name = _canon(name)
        if not name:
            continue
        chunks = []
        for piece in _split_top(raw, "#"):
            piece = piece.strip()
            if len(piece) >= 2 and ((piece[0] == "{" and piece[-1] == "}")
                                    or (piece[0] == '"' and piece[-1] == '"')):
                chunks.append(_bib_unescape(piece[1:-1]))
            else:
                chunks.append(("@" + piece.lower(), _bib_unescape(piece)))
        out[name] = chunks
    return out


def _resolve(fields: dict, macros: dict) -> dict:
    """Resolve each field's concatenated chunks, expanding @string macros."""
    resolved = {}
    for name, chunks in fields.items():
        text = ""
        for chunk in chunks:
            if isinstance(chunk, tuple):
                text += macros.get(chunk[0], chunk[1])
            else:
                text += chunk
        resolved[name] = text.strip()
    return resolved


def _bib_names(value: str) -> list[str]:
    if not value:
        return []
    out = []
    for part in re.split(r"\s+and\s+", value):
        part = part.strip()
        if part and part.lower() != "others":
            out.append(part)
    return out


def bib_entry_count(text: str) -> int:
    """How many real (non-@string/@comment) entries a .bib contains."""
    return sum(1 for etype, _k, _b in _bib_entries(text)
               if etype not in ("string", "comment", "preamble"))


def _bib_extent(pages: str | None) -> str | None:
    """BibTeX ``pages`` -> an extent that reads correctly either way.

    ``311--338`` is a page range in a larger work; a bare ``599`` on a book is
    its page count. "pp. 599" would be wrong for the second.
    """
    if not pages:
        return None
    pages = pages.strip()
    if re.search(r"\d\s*[–-]\s*\d", pages):
        return f"pp. {pages}"
    return f"{pages} p."


def parse_bib(text: str, source_path: str = "") -> Bibliography | None:
    """Parse a BibTeX file as one document's reference.

    A sidecar describes one document, so the **first** real entry is used;
    :func:`load_bibliography` warns when a file holds more than one. Non-standard
    fields (``shelfmark``, ``repository``, ``record_origin``) are honoured, and
    ``crossref`` is resolved so an `@inbook` chunk inherits its parent volume.
    """
    entries = _bib_entries(text)
    macros: dict = {}
    real: list[tuple[str, str, dict]] = []
    for etype, key, body in entries:
        fields = _bib_fields(body)
        resolved = _resolve(fields, macros)
        if etype == "string":
            macros.update({(k if k.startswith("@") else "@" + k): v
                           for k, v in resolved.items()})
            continue
        real.append((etype, key, resolved))
    if not real:
        return None

    by_key = {key.lower(): fields for _t, key, fields in real if key}
    # crossref: an @inbook inherits its parent's publisher/date/series
    for etype, key, fields in real:
        parent_key = (fields.get("crossref") or "").strip().lower()
        parent = by_key.get(parent_key)
        if not parent:
            continue
        for field, value in parent.items():
            if field == "crossref":
                continue
            target = "booktitle" if field == "title" else field
            fields.setdefault(target, value)

    etype, _key, f = real[0]
    bib = Bibliography(source_format="bib", source_path=source_path)
    bib.title = f.get("title") or None
    bib.part_number = f.get("volume") or None
    # Role words, not MARC codes: this is the readable form, and it round-trips
    # a MODS `roleTerm type="text"` unchanged.
    for role, field in (("author", "author"), ("editor", "editor"),
                        ("translator", "translator")):
        for name in _bib_names(f.get(field, "")):
            bib.creators.append(Name(name=name, role=role))
    bib.type = "text"
    bib.genre = _BIB_GENRE.get(etype, etype)
    bib.place = f.get("address") or None
    bib.publisher = (f.get("publisher") or f.get("institution")
                     or f.get("school") or f.get("organization") or None)
    bib.date_issued = f.get("year") or f.get("date") or None
    bib.edition = f.get("edition") or None
    bib.language = f.get("language") or None
    bib.extent = f.get("extent") or _bib_extent(f.get("pages"))
    for field, kind in (("isbn", "isbn"), ("issn", "issn"), ("doi", "doi")):
        if f.get(field):
            bib.identifiers.append(Identifier(value=f[field], type=kind))
    bib.url = f.get("url") or None
    bib.repository = f.get("repository") or f.get("library") or None
    bib.shelfmark = f.get("shelfmark") or f.get("callnumber") or None
    bib.rights = f.get("rights") or f.get("copyright") or None
    bib.classification = f.get("classification") or None
    bib.citation_override = f.get("bibliographiccitation") or None
    bib.record_id = f.get("recordid") or None
    # BibTeX has no provenance field. A pasted entry almost always came from
    # somewhere (Zotero, a library catalogue) rather than being typed, so the
    # honest default is "imported, not reviewed" — reported by `pha bib`, and
    # not badged in citations. Say `record_origin = {human-supplied}` to verify.
    bib.record_origin = f.get("recordorigin") or "imported"
    for key_name in ("subject", "keywords"):
        for topic in re.split(r"[;,]\s*", f.get(key_name, "")):
            if topic.strip():
                bib.subjects.append(topic.strip())

    host_title = f.get("booktitle") or f.get("journal") or f.get("series") or None
    host_volume = f.get("number") or None
    if host_title or host_volume:
        bib.host = Host(
            title=host_title,
            volume=host_volume,
            date=f.get("year") if f.get("booktitle") or f.get("journal") else None,
            publisher=f.get("publisher") if f.get("booktitle") else None,
        )
    return bib if bib.has_content() else None


def to_dc_json(bib: Bibliography, *, qualified: bool = False) -> dict:
    """Emit a JSON sidecar body for a record — the editable form of a reference.

    MODS is a machine interchange format (it is what Zotero exports); this is
    the surface a person actually writes and edits. Empty fields are omitted so
    the file stays short and obvious.

    Keys are plain and readable by default (``part_number``, ``shelfmark``,
    ``record_origin``); ``qualified=True`` emits Dublin Core JSON-LD with
    ``dcterms:``/``pha:`` prefixes plus an ``@context``, for handing to another
    tool. Both spellings parse back identically (see :func:`_canon`).
    """
    p = (lambda k: f"pha:{k}") if qualified else (lambda k: k)
    d = (lambda k: f"dcterms:{k}") if qualified else (lambda k: k)
    out: dict = {}
    if qualified:
        out["@context"] = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "dcterms": "http://purl.org/dc/terms/",
            "pha": "https://github.com/joaquimrcarvalho/personal-historical-archive/ns#",
        }

    def put(key: str, value):
        if value not in (None, "", [], {}):
            out[key] = value

    put(d("title"), bib.title)
    put(p("sub_title"), bib.sub_title)
    put(p("part_number"), bib.part_number)
    put(p("part_name"), bib.part_name)
    if bib.creators:
        if qualified:
            out[d("creator")] = [
                {p("name"): n.name, **({p("role"): n.role} if n.role else {})}
                for n in bib.creators
            ]
        else:
            out["creators"] = [
                {"name": n.name, **({"role": n.role} if n.role else {})}
                for n in bib.creators
            ]
    put(d("type"), bib.type)
    put(p("genre"), bib.genre)
    put(p("place_of_publication"), bib.place)
    put(d("publisher"), bib.publisher)
    put(p("date_issued"), bib.date_issued)
    put(p("edition"), bib.edition)
    put(d("language"), bib.language)
    put(d("format"), bib.form)
    put(d("extent"), bib.extent)
    if bib.identifiers:
        if qualified:
            out[d("identifier")] = [
                {"@value": i.value, **({"type": i.type} if i.type else {})}
                for i in bib.identifiers
            ]
        else:
            out["identifiers"] = [
                {"value": i.value, **({"type": i.type} if i.type else {})}
                for i in bib.identifiers
            ]
    put(p("repository"), bib.repository)
    put(p("shelfmark"), bib.shelfmark)
    put(p("url"), bib.url)
    put(d("rights"), bib.rights)
    put(p("classification"), bib.classification)
    if bib.subjects:
        put(d("subject"), list(bib.subjects))
    if bib.host is not None:
        host: dict = {}
        for key, value in (
            (d("title"), bib.host.title),
            (p("volume_number"), bib.host.volume),
            (p("date_issued"), bib.host.date),
            (d("publisher"), bib.host.publisher),
            (p("shelfmark"), bib.host.shelfmark),
        ):
            if value:
                host[key] = value
        if bib.host.identifiers:
            host[p("identifier")] = [i.value for i in bib.host.identifiers]
        put(d("is_part_of"), host or bib.host.title)
    put(d("bibliographic_citation"), bib.citation_override)
    put(p("record_id"), bib.record_id)
    put(p("record_origin"), bib.record_origin)
    return out


def to_dc_json_text(bib: Bibliography, *, qualified: bool = False) -> str:
    """The sidecar file content for a record, newline-terminated."""
    return json.dumps(to_dc_json(bib, qualified=qualified),
                      ensure_ascii=False, indent=2) + "\n"


#: genre -> BibTeX entry type, for the write direction.
_GENRE_TO_BIB = {
    "book": "book", "chapter": "inbook", "article": "article", "thesis": "phdthesis",
    "report": "techreport", "manual": "manual", "unpublished": "unpublished",
    "manuscript": "unpublished", "misc": "misc", "collection": "book",
}


def _bib_escape(value: str) -> str:
    """Escape the characters BibTeX treats as markup.

    Values are written as UTF-8, so accented letters need no LaTeX escapes —
    which is what keeps the file readable and editable. Only the genuinely
    structural characters are escaped.
    """
    out = value.replace("\\", " ")
    for ch in "&%$#_":
        out = out.replace(ch, "\\" + ch)
    return out.replace("{", r"\{").replace("}", r"\}")


def bibtex_key(bib: Bibliography) -> str:
    """A stable, ASCII citation key: author + year + a title word."""
    parts: list[str] = []
    if bib.creators:
        family = re.split(r"[,\s]+", bib.creators[0].name.strip())[0]
        if family:
            parts.append(family)
    if bib.date_issued:
        year = re.search(r"\d{4}", bib.date_issued)
        if year:
            parts.append(year.group(0))
    if bib.title:
        word = next((w for w in re.findall(r"[^\W\d_]{4,}", bib.title, re.UNICODE)), "")
        if word:
            parts.append(word)
    key = "".join(parts) or "reference"
    key = unicodedata.normalize("NFKD", key)
    key = "".join(c for c in key if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]", "", key) or "reference"


def to_bibtex(bib: Bibliography, *, key: str | None = None,
              origin: str | None = None) -> str:
    """Emit a BibTeX entry for a record.

    This is the format that lets a reference be **drafted from a scan** with no
    external tool — an agent (or a person) reading a title page can write one —
    while staying easy for a human to correct afterwards. Written as UTF-8, so
    accented titles stay legible; :func:`parse_bib` reads it straight back.

    `origin` overrides the recorded provenance; an agent drafting a reference
    must pass ``agent-drafted-unverified`` so the citation carries the warning.
    """
    entry = _GENRE_TO_BIB.get((bib.genre or "").strip().lower(), "book")
    fields: list[tuple[str, str]] = []

    def put(name: str, value):
        if value:
            fields.append((name, _bib_escape(str(value))))

    put("title", bib.title)
    authors = [n.name for n in bib.creators if (n.role or "").lower().startswith(("aut", "author"))]
    editors = [n.name for n in bib.creators if (n.role or "").lower().startswith(("edt", "editor"))]
    translators = [n.name for n in bib.creators
                   if (n.role or "").lower().startswith(("trl", "translator"))]
    # a creator with no role is treated as an author, which is the common case
    for n in bib.creators:
        if not n.role and n.name not in authors:
            authors.append(n.name)
    put("author", " and ".join(authors))
    put("editor", " and ".join(editors))
    put("translator", " and ".join(translators))
    put("volume", bib.part_number)
    # an extent that was derived from `pages` goes back as `pages`
    extent = (bib.extent or "").strip()
    pages = (re.match(r"^pp?\.\s*(.+)$", extent)
             or re.match(r"^(.+?)\s*p\.$", extent))
    if pages:
        put("pages", pages.group(1).strip())
    elif extent:
        put("extent", extent)
    if (bib.genre or "").lower() in ("chapter", "article") and bib.host and bib.host.title:
        put("booktitle", bib.host.title)
    elif bib.host and bib.host.title:
        put("series", bib.host.title)
    if bib.host and bib.host.volume:
        put("number", bib.host.volume)
    put("address", bib.place)
    put("publisher", bib.publisher)
    put("year", bib.date_issued)
    put("edition", bib.edition)
    put("language", bib.language)
    for ident in bib.identifiers:
        kind = (ident.type or "").lower()
        if kind in ("isbn", "issn", "doi"):
            put(kind, ident.value)
    put("url", bib.url)
    # non-standard but round-tripped, and genuinely useful for an archive
    put("repository", bib.repository)
    put("shelfmark", bib.shelfmark)
    put("rights", bib.rights)
    put("record_id", bib.record_id)
    put("record_origin", origin if origin is not None else bib.record_origin)
    if bib.subjects:
        put("keywords", ", ".join(bib.subjects))
    # non-standard, but it round-trips; `note` would collide with ordinary notes
    put("bibliographiccitation", bib.citation_override)

    body = "".join(f"\n  {name} = {{{value}}}," for name, value in fields)
    return f"@{entry}{{{key or bibtex_key(bib)},{body}\n}}\n"


# --------------------------------------------------------------------------- rendering

_VOLUME_RE = re.compile(
    r"^\s*(\d+|[IVXLCDM]+)\s*[.°ºo\"']*\s*(?:VOL\.?|vol\.?)?\s*(\([^)]*\))?",
)


def clean_volume(raw: str | None) -> str | None:
    """Normalise a volume designator for display.

    Source records are inconsistent — ``1.° VOL. (1499-1522)``, ``4." VOL.
    (1548- 1550)``, ``9.o VOL.`` — so the sidecar stays faithful and the
    *renderer* tidies. Anything unrecognisable is passed through unchanged.
    """
    if not raw:
        return None
    raw = raw.strip()
    m = _VOLUME_RE.match(raw)
    if not m:
        return raw
    number, rng = m.group(1), m.group(2)
    if rng:
        return f"{number} ({rng.strip()[1:-1].replace(' ', '')})"
    return number


def _legacy_citation(filename: str | None, doc_id, page_no, variant_label: str | None) -> str:
    """Exactly the string `pha cite` produced before bibliographic sidecars."""
    label = f" ({variant_label})" if variant_label else ""
    return f"{filename} — doc {doc_id}, p. {page_no}{label}"


def _sentence(text: str) -> str:
    """End a citation element with one full stop, never two.

    A shelfmark or title may already end in one (``BNP RES. 1234 V.``), and
    doubling it looks like a typo in a citation.
    """
    text = text.strip()
    return text if text.endswith((".", "!", "?", "…")) else text + "."


def _compose(bib: Bibliography) -> str:
    parts: list[str] = []
    names = "; ".join(n.name for n in bib.creators if n.name)
    if names:
        parts.append(_sentence(names))
    title = " : ".join(x for x in (bib.title, bib.sub_title) if x)
    if title:
        parts.append(_sentence(title))

    # The containing work — a journal, or the book a chapter sits in. For an
    # article or a chapter this is NOT optional: without the journal name the
    # citation cannot be found, and the page range is what locates the item
    # inside it. Both used to be captured and then silently dropped.
    #
    # For a book the same `host` is usually a `series` that either repeats the
    # book's own title (the multi-volume case) or is a decorative series
    # statement, and `extent` is a physical description — so neither is
    # rendered here. That keeps existing book citations stable.
    genre = (bib.genre or "").strip().lower()
    contained = genre in ("article", "chapter")
    host_title = (bib.host.title or "").strip() if bib.host else ""
    if contained and host_title:
        parts.append(_sentence(f"In: {host_title}" if genre == "chapter" else host_title))

    volume = bib.part_number or (bib.host.volume if bib.host else None)
    volume = clean_volume(volume)
    if volume:
        parts.append(_sentence(f"vol. {volume}"))
    if contained and bib.extent:
        parts.append(_sentence(bib.extent))

    imprint: list[str] = []
    if bib.place and bib.publisher:
        imprint.append(f"{bib.place}: {bib.publisher}")
    elif bib.place or bib.publisher:
        imprint.append(bib.place or bib.publisher)  # type: ignore[arg-type]
    if bib.date_issued:
        imprint.append(bib.date_issued)
    if imprint:
        parts.append(_sentence("(" + ", ".join(imprint) + ")"))
    where = ", ".join(x for x in (bib.repository, bib.shelfmark) if x)
    if where:
        parts.append(_sentence(where))
    return " ".join(parts)


def compose_reference(bib: Bibliography) -> str:
    """The reference alone, without the archive's ``— doc N, p. P`` locator.

    This is what gets stored in the snapshot and shown as a document-level
    reference (library front matter, the viewer's overview, MCP).
    """
    return (bib.citation_override or "").strip() or _compose(bib)


def format_citation(
    bib: Bibliography | None,
    *,
    doc_id=None,
    page_no=None,
    variant_label: str | None = None,
    filename: str | None = None,
    mark_agent_drafted: bool = True,
) -> str:
    """Render one page's citation.

    With no sidecar this returns byte-for-byte what `pha cite` always
    returned. With one, the composed reference is prefixed and the archive's
    own locator (``— doc N, p. P (variant)``) is kept, so a citation still
    says which reading it rests on.

    A **model-drafted** reference is badged inline; one merely imported from
    the owner's library is not (it is reported by `pha bib` instead).
    """
    locator = f"doc {doc_id}, p. {page_no}"
    if variant_label:
        locator += f" ({variant_label})"
    if bib is None or not bib.has_content():
        return f"{filename} — {locator}"
    base = compose_reference(bib)
    text = f"{base} — {locator}" if base else locator
    if mark_agent_drafted and bib.is_agent_drafted():
        text += " [unverified reference]"
    return text
