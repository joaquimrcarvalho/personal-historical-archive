# Enhancement request — one malformed model response must not kill a scan

**Status:** draft, not implemented. Two measured defects from the Documenta
Indica / Francisco Rodrigues run of 2026-09-15. **Date:** 2026-09-15.
**Written against:** pha 0.24.0.

## 1. Problem

Two related failures. The first is a defect in pha; the second is why nobody
noticed it for hours.

### D1 — a raw `TypeError` escapes `_openai_chat` and aborts the whole scan

`src/personal_historical_archive/model_client.py:629`:

```python
def _openai_chat(self, payload: dict[str, Any]) -> str:
    """POST an OpenAI-format chat payload and return the text."""
    data = self._post("/chat/completions", payload)
    try:
        return _strip_think(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, AttributeError) as e:
        raise ModelError(f"Unexpected chat response: {data!r}") from e
```

When a remote OpenAI-style endpoint answers HTTP 200 with a **null message**
(observed repeatedly from MiniMax-M3, which sometimes returns
`{"choices":[{"message":{"role":"assistant"}}]}` and sometimes
`"message": null`), the lookup becomes `None["content"]` → **`TypeError`**,
which is *not* in the caught tuple. The sibling function catches it:

```python
# _anthropic_chat, line 619
        except (KeyError, TypeError) as e:          # ← has TypeError
            raise ModelError(f"Unexpected anthropic response: {data!r}") from e
```

So this is an **inconsistency between two sibling parsers**, not a design
choice. The same gap exists at line 737 (`chat_vision`'s OpenAI path) — see R1.

**Consequence.** `edit_document` → `client.chat_text` → `_openai_chat` raises a
bare `TypeError`. `ingest_file` guards the per-page call with
`except (ModelError, FilterError)` (line 785) — a `TypeError` is neither, so it
propagates out of `scan_once` and **kills the entire run** after however many
pages had succeeded, instead of failing that one page and continuing.

### D2 — `status = done` is committed *before* the editor and indexer run

`src/personal_historical_archive/ingest.py`:

```python
809  db.set_document_status(conn, doc_id, "done", prompt_source=prompt_source)
810  conn.commit()
811  edit_document(cfg, conn, doc_id, verbose=verbose)   # editor pass
812  index_document(cfg, conn, doc_id, verbose=verbose)  # indexes raw + edited
813  write_document_pages(cfg, conn, doc_id)
```

A crash at 811 or 812 leaves the document **`done` with no edited variant and
`chunks = 0`**. `pha status` reports it healthy, and so does any driver that
checks `status != 'done'`.

Compounding it: `index_document()` is called from **only two places** —
`ingest_file` (812) and `reindex_all` (2540). The standalone editor command
(`cli.py:1727 cmd_edit` → `ingest.py:1821 edit_documents_under`) **never
re-indexes**, so re-editing a document cannot repair a missing index; a separate
`pha reindex` is required and nothing says so.

## 2. Motivating case (measured, 2026-09-15)

A chained scan of `collections/documenta-indica` (3 vols, 2 697 pp) followed by
`collections/reference-works/francisco-rodrigues-hcjap` (7 vols, 4 598 pp).

The driver script's own verdict, from its DB check and `pha`'s summary:

```
+ historia_da_companhia_de_jesus-t4-v1.pdf (612 pages, ...)
scanned 7 file(s): {'ingested': 7, 'skipped': 0, 'error': 0}
[chain] francisco-rodrigues documents=7 unfinished=0 (need >= 7 and 0)
[chain] francisco-rodrigues ALL DONE  21:02:39
[chain] CHAIN FINISHED rc=0  21:02:39
```

The archive totals agreed: `documents=47 done=47 error=0 pages=24468 chunks=86352`.

Reality, one document in:

| doc | volume | status | pages | chunks |
|---|---|---|---|---|
| 19 | DOCUMENTA-INDICA-1540-49 | done | 1011/1011 | 4 267 |
| 56 | DOCUMENTA-INDICA-1550-1553 | done | 725/725 | 3 359 |
| **57** | **DOCUMENTA-INDICA-1553-1557** | **done** | **961/961** | **0** |

The library showed exactly where it died:

```
edited-documenta-indica-ocr             39   ← editor crashed at page ~39/961
transcription-ocr@liteparse            961   ← transcription was complete
edited-documenta-indica-ocr@deepseek-v4-flash   (absent — never finished)
```

stderr from the run:

```
File ".../ingest.py", line 811, in ingest_file
    edit_document(cfg, conn, doc_id, verbose=verbose)
File ".../ingest.py", line 1769, in edit_document
    out = client.chat_text(editor.model, prompt, editor.temperature, editor.max_tokens, ...)
File ".../model_client.py", line 712, in chat_text
TypeError: 'NoneType' object is not subscriptable
```

(Line numbers in that traceback do **not** line up with the source — the
editable checkout's files changed after the module was imported. This is itself
an argument for naming the defect by symbol, and for a driver that checks data
rather than trusting a status.)

**Cost:** 961 pages of editor calls must be repeated, a `pha reindex` is still
required afterwards, and a 4 598-page collection reported success over a broken
volume. The failure was caught only because a human read the stderr traceback
and then queried the DB by hand.

## 3. Requirements

- **R1 — no raw exception from a malformed response.** A chat response whose
  `message` is null/absent, or whose `content` is absent, must raise
  `ModelError` (the documented, per-page-recoverable failure), never `TypeError`
  or `AttributeError`. Applies to `_openai_chat` (629) **and** the OpenAI path
  of `chat_vision` (737), matching `_anthropic_chat` (626).
- **R2 — one bad page must not end the run.** A model failure on page *k* must
  be recorded against that page and the document continue, which is already the
  contract of the `except (ModelError, FilterError)` block at 785 — R1 is what
  makes the contract hold for this class of response.
- **R3 — `done` must mean "pipeline finished".** A document whose editor or
  indexer never ran must not be reported `done`. Either set the status after
  step 812, or introduce a distinct terminal state (e.g. `indexed`) so
  `done`-but-unindexed is unrepresentable.
- **R4 — re-editing must not silently skip indexing.** `pha edit` should index
  what it re-edited, or print an explicit, unmissable line that it did not and
  that `pha reindex` is required.
- **R5 — the health check must look at data, not just status.** Any driver or
  status surface that claims a collection is complete must verify that chunks
  exist for each document.

## 4. Proposed changes

### 4.1 The patch (R1)

```diff
--- a/src/personal_historical_archive/model_client.py
+++ b/src/personal_historical_archive/model_client.py
@@ -631,7 +631,7 @@ class ModelClient:
         data = self._post("/chat/completions", payload)
         try:
             return _strip_think(data["choices"][0]["message"]["content"])
-        except (KeyError, IndexError, AttributeError) as e:
+        except (KeyError, IndexError, AttributeError, TypeError) as e:
             raise ModelError(f"Unexpected chat response: {data!r}") from e
```

The same one-word addition at line 737 (`chat_vision`).

### 4.2 Belt-and-braces: tolerate a null message explicitly (R1)

Catching `TypeError` converts the crash into a per-page `ModelError`, but a
model that emits nothing on a blank page is *normal*, not exotic (see the
`franco-imagens` blank-plate case in `pha-post-filter-replay-enhancement-request.md`).
Consider a shared extractor used by both chat paths:

```python
def _openai_text(data: dict) -> str:
    """Text of an OpenAI-style chat response; "" when the model said nothing."""
    try:
        msg = data["choices"][0].get("message") or {}
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise ModelError(f"Unexpected chat response: {data!r}") from e
    return _strip_think(msg.get("content") or "")
```

Note the policy question this opens: today a null message is an *error*; with
4.2 it becomes an *empty page*. That is the right behaviour for a blank plate,
but it must be a deliberate decision, and an empty result must still be recorded
as an empty page rather than silently skipped.

### 4.3 Make `done` honest (R3)

Move line 809's `set_document_status(..., "done")` to after `index_document`
(812), or add an explicit stage so that a document is only `done` once its
edited variant and its chunks exist. Whichever is chosen, `--reprocess` and the
resume logic must treat the new state consistently.

### 4.4 Let `pha edit` index (R4)

Either call `index_document()` for each document `edit_documents_under` touched,
or end the command with an explicit `not re-indexed; run: pha reindex --path …`.

### 4.5 Harden the archive-side driver (R5)

`<archive>/chain-queue.sh` currently judges success with:

```sql
SELECT count(*) FROM documents WHERE dir_path LIKE '<coll>%' AND status != 'done';
```

Replace the "unfinished" query with one that also catches `done`-but-unindexed:

```sql
SELECT count(*) FROM documents d
WHERE d.dir_path LIKE '<coll>%'
  AND ( d.status != 'done'
        OR (SELECT count(*) FROM chunks c WHERE c.document_id = d.id) = 0 );
```

This single change would have caught doc 57 at 21:02 instead of at 23:00, and
turned a silent success into a visible failure. (This script is archive-side,
not part of pha; it is listed here because it is the operational half of the
same defect.)

## 5. Acceptance criteria

- **Unit:** `_openai_chat` given `{"choices":[{"message":None}]}` and
  `{"choices":[{"message":{"role":"assistant"}}]}` raises `ModelError`, never
  `TypeError`. Same for the `chat_vision` OpenAI path.
- **Injection:** force the editor to fail on page *k* of a 3-page document;
  assert (i) no traceback escapes, (ii) the run continues past the failure,
  (iii) the document is not reported `done`/complete while its index is empty,
  (iv) chunks reflect reality.
- **Detection:** a `done` document with zero chunks is surfaced by the status
  surface (or is unreachable by construction, per R3).
- **Editor:** after `pha edit` on a document with a missing index, the document
  is either indexed or the command says plainly that it is not.
- **Regression:** a scan of a document whose model returns a null message on
  page 1 completes and records that page, rather than aborting at page 1.

## 6. Related defect found in the same session

`pha scan --path <a single .pdf>` silently scans **zero** files.

```
$ pha scan --path collections/franco-imagens/franco-imagem-virtude-coimbra-1719-v2.pdf
target: collections/franco-imagens/franco-imagem-virtude-coimbra-1719-v2.pdf
scanned 0 file(s): {'ingested': 0, 'skipped': 0, 'error': 0}
```

`scan_once` (ingest.py:2485) calls `discover(..., root=scan_root)`, and
`discover` (353) only handles directory roots — it enumerates with
`base.rglob("*")` (389, 398), so a *file* root yields `[]`. The `pha edit` path
does not have this bug because `_documents_under` (2578) handles
`root.is_file()` explicitly. `AGENTS.md` documents single-file `--path` as
supported ("`documents/myfile.pdf`"), so the documentation and the behaviour
disagree.

Fix, mirroring `_documents_under`:

```python
base = dropbox if root is None else root
if not base.exists():
    return []
if base.is_file():
    return [] if _excluded(base) else [base]
```

## 7. Out of scope

- The upstream MiniMax behaviour (returning a null `message`). pha only needs
  not to crash on it.
- Whether an empty model response is an error or an empty page — R1 requires
  only that it not be a `TypeError`; the policy choice belongs with §4.2.
- Re-running the interrupted work (doc 57's editor pass and the subsequent
  `pha reindex`) — operational, handled separately.
