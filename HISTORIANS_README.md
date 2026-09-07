# pha for historians — a step-by-step guide

**pha** (Personal Historical Archive) keeps your manuscripts, old books and
maps in a local archive: you drop files into a folder, a vision model reads
each page, a text model can modernize or translate the transcriptions, and
everything becomes searchable by your AI assistant.

You do not need to touch the command line yourself — you work through **your
favourite AI agent** (Claude, ChatGPT, Cursor, …). The steps below tell you
what to ask, and each section has a block you can copy straight into the chat.

---

## Before you start

You need:

- A computer with **macOS or Windows**
- An AI assistant you trust with file operations
- **The AI models pha uses** — the models that read and transform your pages.
  There are two ways to get them, and you can use both side by side:
  - **Local models**, run on your own machine by **LM Studio** (free, from
    lmstudio.ai). Nothing ever leaves your computer, but your hardware limits
    which models can run.
  - **Remote models**, hosted by a provider (MiniMax, OpenRouter, OpenAI, …)
    and reached over the internet. They are far more powerful, but you need an
    **API key** from the provider and the page images are sent there. See
    step 1b.

The archive itself always lives on **your** computer. Only the pages you
choose to process with a remote model leave it; local processing stays
entirely on your machine.

---

## 1. Set up the models and install pha

You can ask your agent to do all of it. Three short steps, in order (step 1a —
installing LM Studio — is only needed if you will run models locally; for
remote models you can skip it):

### 1a. Install LM Studio (if you don't have it)

```
Please install LM Studio on my computer: download it from lmstudio.ai,
install it, and open it so its local server can run. Tell me when it is ready.
```

### 1b. Choose the best models for your documents

Different vision and text models are better for different material, and you are
not limited to what runs on your own computer: pha can use **models hosted
remotely** as well as local ones. That matters more than you might expect.

Two important facts in September 2026:

- **Reading handwritten manuscripts well needs a powerful vision model.** The
  best current vision models (large multi-modal models) are far too big to run
  on a normal laptop or desktop, so **today manuscript reading almost always
  uses a remote model**. This will tend to change in the future as stronger
  models run locally — but don't be surprised that the manuscript-reading model
  is not on your machine yet. For **old printed / typeset text**, by contrast,
  a local vision model (or OCR — see below) is normally fine; printed letters
  and shapes don't need that much judgement.
- **Editing/translating text is much lighter**, and a local text model is often
  enough (it can also be remote if you prefer).
- **Remote image processing costs money.** A page image is a lot of tokens, so
  a remote vision model bills more per page than a local one (this is a
  current limitation of how vision models are priced). It is usually worth it
  for handwritten manuscripts; for clean printed text a local model or OCR is
  much cheaper.

So the practical setup for most historians right now is: a **remote vision
model** for handwritten **manuscripts**, a **local vision model (or OCR)** for
old **printed / typeset text**, and a **local text model** to edit everything
(remote if you prefer). Use whichever fits each collection — you are not locked
into one model for the whole archive. Below are prompts for both local and
remote cases.

#### Local models (everything on your machine)

Ask your agent to research and recommend:

```
Research and recommend the best LOCAL models for my archive, considering my
computer specs (e.g. MacBook Air M2, 24 GB RAM):

1. a VISION model for reading pages of [describe your documents, e.g. printed
   19th-century Portuguese books and 17th-century manuscript letters] —
   give me 2-3 options with a clear recommendation
2. a TEXT model for editing transcriptions (modernizing old Portuguese,
   translating to English) — 2-3 options with a recommendation
3. an EMBEDDING model for search (a small one is fine)

Then tell me exactly which models to download in LM Studio and their names
in the LM Studio catalog.
```

A good starting point, already configured in pha by default: the vision model
`qwen/qwen3-vl-8b` (excellent at reading historical text), the embedding model
`text-embedding-nomic-embed-text-v1.5`, and the text/editing model
`amalia-9b-0626-dpo` (or `google/gemma-4-e4b`). Keep these if the research
agrees, or switch per the recommendation.

#### Remote models (hosted by a provider)

For the reading model in particular, this is the route most historians will
take today. To use a remote model you need an **API key** from its provider
(an account with MiniMax, OpenRouter, OpenAI, … gives you one; some are paid).
Give your agent a prompt like this:

```
Set me up to use a REMOTE vision model to read my manuscripts. Please:

1. Research and recommend 2-3 strong REMOTE vision models for reading
   [describe your documents, e.g. 17th-century manuscript letters] and pick one.
2. Tell me which provider hosts it and exactly how to get an API key
   (create an account / buy credits / copy the key from the provider's
   dashboard). Do not invent a key — tell me what to do.
3. Once I give you the key, store it safely (run: pha key --set VARNAME,
   where VARNAME is a name pha recognizes, e.g. MINIMAX_API_KEY or
   OPENROUTER_API_KEY). Keep the key secret — do not paste it into documents.
4. Create the model file (models/<id>.md) with the provider's base_url, the
   server-side model name, api_key: "${VARNAME}", and api_style (openai by
   default; anthropic for MiniMax). Set the vision limits (max_vision_px,
   vision_jpeg_quality, context_tokens) to suit the model.
5. Point my palaeographer/editor rules at it in pha.yaml, then verify with
   `pha test` on a couple of pages. Tell me the result.
```

If you would rather not write all that, ask your agent to run the
**Model Helper** prompt (see the note below) — it asks you a few plain-language
questions and produces the model + rules files for you, local or remote, and
safely stores the API key.

> **Model Helper prompt for your agent.** A ready-to-use interview prompt that
> guides an agent through configuring any pha model — local (LM Studio /
> Ollama) or remote (MiniMax, OpenRouter, OpenAI, …) — is in
> `prompts/model-helper.md`. Have the agent read that file and follow it: it
> asks for the provider, the endpoint and model name, whether it needs vision,
> and (for remote) safely stores the API key with `pha key --set` and writes
> the model + rules + `pha.yaml` files for you.

### 1c. Install pha (from GitHub)

Copy this into your agent's chat and let it do the work:

```
Please install the "personal-historical-archive" (pha) program from
https://github.com/joaquimrcarvalho/personal-historical-archive

Steps:
1. Clone the repository to a folder of your choice, e.g. ~/develop/personal-historical-archive
2. Install `uv` if it is not present, create a Python virtual environment
   in the project and install the package (uv pip install -e .)
3. Check that LM Studio is running with its local server on port 1234 and
   that the chosen models are available (the vision model, the embedding
   model, and the text/editing model). Load them if needed.
4. Verify the installation and tell me the result of `pha status`.
```

What you should see afterwards: a short report that the archive is ready and
which palaeographers and editors are configured (`qwen-local` by default,
plus a `modern-portuguese` editor).

**Windows note:** the commands are the same, only the environment folder
differs (`Scripts\python.exe` instead of `bin/python`). Your agent will
handle this.

---

## 2. Put your documents in the archive

pha expects a simple folder layout. Tell your agent:

```
Create these folders in the archive (inside the "dropbox" folder):
  dropbox/documents/          — individual documents
  dropbox/collections/<NAME>/ — one folder per collection, e.g. "letters-from-missons"

Then copy my files in, following these rules:
- a PDF or an image file = one document
- a FOLDER containing only images = one document whose pages are those images
  (put it inside documents/ or inside a collection)
- anything that belongs together historically (e.g. all letters of one
  correspondence) goes into its own collection folder
- tell me where each file ended up
```

Rules of thumb for your own use:

- **One document** = a PDF (many pages) **or** an image (one page) **or** a
  folder of page-scan images (each image becomes a page).
- **A collection** = a folder that groups related documents, e.g.
  `collections/missons-do-oriente/`. The folder name becomes a way to filter
  searches.
- Keep the originals somewhere safe; pha reads a copy you place in the
  dropbox and never modifies your source files.

---

## 3. Set up palaeographers and editors

Two kinds of "helpers" read and improve your documents:

- **Palaeographer** — a vision model that *reads* a page image and transcribes
  it. Different palaeographers can be specialised for different hands or
  languages (e.g. 17th-century Portuguese secretary hand). A palaeographer
  **only transcribes faithfully** — it does not expand abbreviations,
  modernize or translate; and its notes are brief reading notes (language,
  script, difficult words), not entity lists or summaries. Entity lists,
  summaries and structured records come from the editor and encoder.
- **Editor** — a text model that *transforms* the transcription: expand
  abbreviations, convert to modern Portuguese, translate to English,
  normalize names, …

Each is a single text file in the `palaeographers/` or `editors/` folder
(content rules only — it references a model via `model: <id>`). **To add one,
you duplicate the `_sample.md` file, rename it, and edit it** — your agent can
do this for you from a plain-language description.

Tell your agent:

```
Show me the current palaeographers and editors (run: pha palaeographer
and pha editor). Then, for my documents, I want:

[describe what you need, for example:]
- a palaeographer specialised in 17th-century Portuguese secretary hand,
  transcribing faithfully in the original language
- an editor that converts the transcriptions to modern Portuguese

Create the corresponding files by copying palaeographers/_sample.md and
editors/_sample.md, giving them good names, setting `model:` (the model we
chose in step 1b) and temperature, and writing the instructions in the body.
Then SELECT them for my collections: write a pha.yaml sidecar in each
collection folder, e.g.
  palaeographer:
    rules: portuguese-secretary
  editor:
    rules: modern-portuguese
Show me the result of:
  pha palaeographer
  pha editor
```

Notes:

- The **file name** (without extension) is the id used for selection — e.g. a
  file `palaeographers/portuguese-secretary.md` is selected with
  `palaeographer: {rules: portuguese-secretary}` in `pha.yaml`.
- You can use a different palaeographer/editor per **collection** — that is
  how a historian's editorial choice is expressed.
- The base prompt in a palaeographer file defines the output format; the
  document/collection prompt only adds specific aspects (e.g. which fields
  matter, whether to modernize spelling).

### How pha reads a page: LLM vision vs OCR

There are two ways pha can turn a page image into a transcription, and they
behave very differently. You choose per collection in `pha.yaml`.

**1. Vision model (an LLM that reads images) — the default.**

A *vision language model* (e.g. `qwen/qwen3-vl-8b`) reads the page like a
scholar: it sees the whole layout, follows the hand, recognises a damaged or
difficult word *from context*, and writes brief reading notes (language,
script, difficult words). It can transcribe dense handwriting, marginalia and
interlinear notes that plain OCR cannot. It runs on a **local vision model**
(LM Studio) **or a remote one** — see section 1b. Reading difficult manuscripts
today most often needs a remote vision model, because the model strong enough
is too big for a normal computer.

*Best for:* handwritten manuscripts, secretary hands, old scripts, pages full
of annotations — anything where judgement and context matter.

**2. OCR (optical character recognition) — Tesseract or LiteParse.**

An OCR engine matches characters to shapes. It is fast, deterministic, and
runs **locally without any model or LM Studio**. It is excellent at **clean
printed or typeset text** (printed books, typewritten letters, printed
tables). It does **not** use context: it struggles with handwriting, unusual
scripts, damaged or touching letters, and produces no reading notes.

*Best for:* printed/typed documents where you want speed and simplicity, and
don't need paleographic judgement.

**Choosing:** printed books and typewritten text → OCR. Handwritten manuscripts
or pages with notes you want explained → a vision model. You can even use OCR
for one collection and a vision model for another.

**The settings, in pha terms** — both are set exactly the same way: as the
`palaeographer` for a collection, in the `pha.yaml` next to the documents. The
only difference is the model file you point at.

| What | Vision model (LLM) | Tesseract OCR | LiteParse OCR |
|------|--------------------|---------------|---------------|
| Needs LM Studio / a local model | maybe* | no | no |
| Understands handwriting & context | yes | weak | weak |
| Reads marginalia / gives reading notes | yes | no | no |
| Great on printed/typeset text | yes | yes | yes |
| Runs on | your machine *or* a remote provider | your machine | your machine |

\* A vision model runs locally (LM Studio/Ollama) **or** remotely (via an API
key). Only the local option needs LM Studio; a remote one needs a key instead.

In the model file (`models/<id>.md`):

- **Vision model** — `base_url` (an LM Studio/Ollama address for a local model,
  or a provider's API root, e.g. `https://api.minimax.io/v1`, for a remote one,
  plus `api_key` as `"${VARNAME}"`), `model` (the server-side model name),
  optional `max_vision_px` (largest page image sent; keep ≤ the model's
  window), `vision_jpeg_quality`, `api_style`, `context_tokens`, `thinking`.
- **Tesseract** — `engine: tesseract`, `tesseract_lang` (the language(s), e.g.
  `por`, `lat`, `por+lat`), optional `tesseract_psm` (page-segmentation mode).
  Needs the `tesseract` program installed (and its language data).
- **LiteParse** — `engine: liteparse`, `liteparse_lang` (OCR language, e.g.
  `por`, `fra`), optional `liteparse_dpi` (resolution; use `300` for quality).
  Needs the `lit` program installed. The same `lit` CLI comes from either
  `pip install liteparse` or `npm i -g @llamaindex/liteparse` — install with
  whichever toolchain you already use: pha finds engines the same way your
  Terminal does (its own PATH, then the PATH your login shell would provide),
  so any normal install just works. Verify
  with `lit --version` (it must print a LiteParse version, not some other
  `lit`). LiteParse bundles its own Tesseract. When unsure whether an engine
  is installed, run `pha doctor` — it reports what is missing and how to
  install it.

Select it for a collection exactly like any other model:

```yaml
# dropbox/collections/COLX/pha.yaml
palaeographer:
  rules: <rules-file-id>
  model: tesseract        # or: liteparse, or a vision-model id
```

then run `pha scan`. The editor and encoder stages afterwards are unchanged —
they still run on the text/LLM model of your choice.

---

## 4. Process the documents and query the archive

### First run (processing)

Tell your agent:

```
Run the extraction:  pha scan
Then, when it finishes:  pha edit
Then show me:  pha status
```

- `pha scan` reads every page with the palaeographer(s).
- `pha edit` runs the editor over the transcriptions (modernize/translate).
- Large books take time; the work is resumable — if the computer sleeps or is
  restarted, just run `pha scan` again and it continues where it stopped.

### Searching

Ask in plain language, e.g.:

- *"Search the archive for mentions of Malacca."*
- *"In the collection missons-do-oriente, find the pages about Francis Xavier."*
- *"Show me the full transcription of that page."*

Your agent can answer directly if it is connected to the archive (see below),
or you can ask it to run: `pha search "Malacca" --collection missons-do-oriente`

### Letting your agent query directly (MCP)

For agents that support MCP (Claude Desktop, Cursor, …), the archive exposes
its search as tools. Ask your agent:

```
Connect to the local MCP server "personal-historical-archive" using:
  <path-to-project>/.venv/bin/python -m personal_historical_archive mcp
(with the environment variable PHA_HOME set to the project folder)
Then use its tools: pha_search, pha_get_document, pha_list_documents,
pha_scan_now, pha_extraction_status.
```

After that you can simply ask questions and the agent will query the archive
itself.

---

### Using the archive from another machine (remote)

You can also work with the archive **from a different computer** — add
documents, or let an agent there search what is already transcribed — while
the archive and models stay on this machine. You do this by making the
archive available over the network (an "MCP server"). Ask your agent:

```
Make the archive available to another machine:
  pha mcp --transport sse --host 0.0.0.0 --port 8000
and tell me the address this computer's network uses (e.g. 192.168.1.20).
(Optional: first run  pha set dropbox <folders>  if you want to move the
documents folder.)
```

From the other machine, an agent can then connect and, among other things:

- **Upload a document / collection**: send the file to
  `http://<this-machine>:8000/sse` and use the `pha_upload` tool (e.g.
  `pha_upload("document", "myfile.pdf", <base64>)`).
- **See how documents are processed**: `pha_palaeographers`, `pha_editors`,
  `pha_encoders`, `pha_collection_config`.
- **Search**: `pha_search`, `pha_get_document`, `pha_list_documents`.

Keep the network address internal (home/office network or a VPN). The archive
is designed so the heavy AI models only ever run on **your** machine.
See `MCP_CLIENTS.md` for the detailed setup.

---

## Where the results are

For each document, in the `library/` folder, mirroring the dropbox layout.
Each document version has a readable folder named after it and its date
(e.g. `1567-Coimbra_2026-08-22`); pages are named after their source scan
(for a folder of images) or `page-NNN` (for a PDF):

```
library/<collection>/<document>_<date>/transcription-<palaeographer>/502V.md   ← faithful transcription
library/<collection>/<document>_<date>/edited-<editor>/502V.md                 ← modernized / edited
```

Each page file starts with a small header (source file, page number,
palaeographer, editor, status) followed by the text.

---

## Reviewing and correcting the transcriptions

You can read the library files and correct them. There are **two page
variants**, and which one you correct changes what happens next:

- `transcription-<palaeographer>/` — the palaeographer's **original reading**.
  Correcting this fixes a misreading at the source; the editor must then re-run
  to use your corrected text.
- `edited-<editor>/` — the editor's transformed text (modernized / translated).
  Correcting this fixes the final output and pha leaves it as you wrote it.

**You corrected the TRANSCRIPTION (e.g. you spotted an OCR or reading error):**

1. **Edit the transcription file** — open
   `library/.../transcription-<palaeographer>/502V.md` and correct the text
   under the header (keep the header as it is).
2. **Check pending work** — run `pha status`; it tells you how many pages have
   corrections that are not imported yet (✏️ message).
3. **Import your correction** — run `pha review`. Your text becomes the page's
   transcription and the page is marked *reviewed*: `pha scan` will never
   re-read it, even with `--reprocess`.
4. **Let the editor redo that page** — run `pha edit`. It detects the
   transcription changed and re-processes **only that page** from your
   corrected text (nothing else is re-edited).
5. **Make search use the results** — run `pha reindex`.

**You corrected the EDITED text (the editor's final output):**

1. Open `library/.../edited-<editor>/502V.md` and correct the text under the
   header.
2. Run `pha status`, then `pha review`. The corrected edit is marked
   *reviewed*, so neither `pha scan` nor `pha edit` will overwrite it.
3. Run `pha reindex`.

Reviewed pages show `reviewed: true` in their header.

---

## Troubleshooting (quick)

- **Nothing happens / errors about a model** → for a **local** model, make sure
  LM Studio is open and its server is running (port 1234); your agent can test
  it. For a **remote** model, check that the API key was stored (`pha key`) and
  that it is set on the model file (`api_key: "${VARNAME}"`). Then run `pha
  test` to confirm the model responds.
- **Extraction seems stuck** → it is probably waiting while the computer
  slept; run `pha scan` again, it resumes.
- **Search returns nothing** → the extraction may not be finished; check
  `pha status`.
- **Windows** → everything works; only the environment paths differ, which
  your agent handles.

---

*pha is designed so that historians control the editorial choices — which
palaeographer reads, which editor transforms — while the technical work stays
in the hands of your AI assistant.*
