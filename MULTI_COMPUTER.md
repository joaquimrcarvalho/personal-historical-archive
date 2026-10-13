# Using pha with two computers

A plain-language guide to the two ways of spreading pha over more than one
machine. Both are driven through your AI agent in ordinary words — you do not
type the commands yourself.

- **Working together** — pha runs on the computer you use, and the heavy AI
  models run on a more powerful one. Both machines are busy *at the same time*
  while your documents are being read.
- **Taking turns (a "hand-over")** — your computer hands a document to another
  machine, which reads and edits it on its own, and the finished work comes back
  later. The two machines only need to be connected at the two hand-over
  moments.

You can use both, for different collections.

| | **Working together** | **Taking turns** |
|---|---|---|
| Where pha runs | the computer you work on | the computer you work on (it owns the archive) |
| Where the AI models run | the more powerful computer | the more powerful computer |
| Where the documents are | the computer you work on | a copy travels; the original stays put |
| Are both computers connected while pages are read? | **yes, the whole time** | **no** — only at the start and at the end |
| What has to be moved | nothing; page images travel over your network as they are read | a folder going out, and a folder coming back |
| Best when | the machines are in the same place and usually switched on | the powerful machine is elsewhere or always on, and your laptop comes and goes |

In **both** cases the page images stay on your own computers and your network.
Nothing is sent to an outside company unless you deliberately choose an online
model.

---

## 1. Working together: pha on your computer, the models on a bigger one

This is the simpler of the two, and the one to pick when both machines are on
the same desk, the same home network, or the same office — and are usually
switched on.

### How it works

Reading a page means sending its image to an AI model and waiting for the text
back. That model does not have to be on the same computer as pha: it only has to
be reachable over the network. So you can keep pha, your archive and your
documents on a light laptop, and let a powerful desktop do the actual reading.

From pha's point of view this is exactly like using an online provider — the
same setting, only the address is a computer on your own network instead of a
company on the internet.

### What you need

- **The powerful computer** with the models installed, its model server running,
  and its network access switched on so the other computer may connect to it.
  Note its network address and port (port 1234 by default).
- **Your computer** with pha and the archive, on the same network.
- Tell pha which machine serves each model. This matters for more than tidiness:
  pha lets one job use each model server at a time, and it can only respect that
  if it knows that two models live on the same machine.

### How to ask for it

```
Set up my archive so that the heavy models run on my other computer:

1. The powerful computer is at <address> and its model server runs on port
   1234. Check that I can reach it from here, and tell me what is missing if
   I cannot.
2. Point the model files for the palaeographer and the editor at that address
   instead of this machine, and record which machine serves each one.
3. Run `pha doctor` and tell me what it says about the model servers and the
   lock directory.
4. Test one page with `pha test` before we do a real run.

Explain each step in plain language before doing it.
```

### What to watch out for

- **Both computers must be awake and connected while pages are read.** If the
  powerful computer sleeps, the reading stops. pha can be told to wait for a busy
  model server rather than give up, but it will not wake a sleeping computer.
- **Page images cross your network while reading.** That is the point — the model
  needs to see them — but it also means you should keep this on a home or office
  network, or a private connection, rather than an open one.
- **Searching is lighter than reading.** Searching the text by keyword always
  works. Searching by meaning needs the small "embedding" model; if that model
  lives on the other computer and it is switched off, pha quietly answers with
  keyword results and tells you so. That is usually exactly what a laptop away
  from its desktop wants.
- **One model at a time per machine.** A computer that keeps only one model in
  memory cannot serve two jobs at once. pha refuses rather than risk it, naming
  the job that is running; if the powerful machine really can hold two models at
  once, tell pha its capacity.
- **If you use LM Studio's "LM Link"** (which lets one computer use a model
  loaded on another *as if it were local*), the address alone cannot tell pha
  where the model really runs. Say so explicitly in the model file, or pha may
  think two models are on different machines when they are not. The bundled
  `lmstudio-model-locality` skill covers exactly this case.

### Why this is a good arrangement

Your laptop stays the place where the archive lives and where you talk to your
agent, and it never has to be powerful. The expensive hardware is used where it
belongs. You can also let the laptop's *lighter* work — chatting about the
archive, searching, writing notes — use an online model, while the heavy page
reading stays on your own network.

---

## 2. Taking turns: hand a document over, get it back later

Pick this when the powerful machine is somewhere else, or always on, and your
computer comes and goes — or when your computer simply cannot reach the models
and the other machine can.

The idea is a loan, not a gift. Your archive keeps the document; another machine
borrows it, does the reading and editing, and returns the work, which is folded
back into the **same** document — no duplicate, no second copy to reconcile.

**No ssh, no password, no logging in to the other computer.** Every command is
run *on the computer you are sitting at* (or by the agent running there). The two
computers never connect to each other while the work is done — the only thing
that travels is a folder, carried the same way you would carry any other file.
If a technical helper told you this needs remote access, that is not the case.

### How it works, in four steps

1. **At your computer:** pha writes one folder holding the document and the
   *settings* to use (which palaeographer, editor and encoders you chose), and
   marks the document as "out on loan". While it is out, your pha will not touch
   it, so the two computers cannot both work on it by accident.
2. **Carry that folder to the other computer** — whichever way is easiest for
   you (the list is just below). You are copying a folder, nothing more.
3. **At the other computer:** one command imports it, then the work runs —
   reading, then editing, then the structured records, exactly the normal three
   stages — and writes a result folder. You can stop and resume; it is not a
   single long sitting. None of this needs your computer to be on, or reachable.
4. **Carry the result folder back**, and at your computer the work is folded into
   the original document. The pages keep their place, their numbers and their
   citations.

### Carrying the folder across: pick whichever is easiest

All four work, and the commands are identical in every case — only the carrying
differs:

1. **Let pha do it** (needs Tailscale on both computers, free, one-time setup):
   pha sends the folder straight to the other computer and unpacks it there.
   Nothing is copied by hand, no keys, no passwords, no open ports. Both
   computers do have to be awake at that moment, since it is a direct transfer.
2. **A shared folder you both already use** — Dropbox, iCloud Drive, OneDrive, a
   network drive. Copy the folder in on one side, out on the other. (Move the
   files yourself; do not point a sync tool at your archive.)
3. **A USB stick.** The best choice for a big collection, and it needs no network
   at all — which also means the most private.
4. **A zip file** sent by email or chat. Perfectly fine for a document of a few
   hundred pages; too big for thousands.

### If the other computer is always on, let it do the whole job

If the second computer is a machine that stays switched on, you can set it up
**once** so that it notices arriving work, does it, and returns the result by
itself — no one has to sit at it:

```
Set up this computer as my hand-over worker, so it collects handed-out
documents, reads and edits them, and sends the results back on its own. It
should start again by itself when the machine restarts. Explain what you are
installing and where, before doing it.
```

After that, your side is only ever "hand this document over" and "collect the
finished work". (This part uses the same Tailscale link as option 1 above, so
both computers need it.)

### What you need

- **pha installed on both computers**, each one installed *at that computer* —
  the same way you installed it here. Nobody logs in to the other machine to set
  it up, and no remote access is involved at any point.
- **Models on the second computer**, or reachable from it.
- **Somewhere to put the travelling folder** — see the four options above.

### How to ask for it

```
Lend this document to my other computer so it can do the reading:

On this computer (which owns the archive):
  - hand the document over and tell me where the folder is
  - then show me what is currently on loan
  - tell me plainly how to get that folder to the other computer — a USB stick,
    a shared folder, or pha sending it itself if both machines have Tailscale.
    I do not want to set up ssh or remote access to the other machine.

On the other computer (where the models are):
  - import the folder, do the reading/editing/records work in the background
    where it can, and tell me when the result folder is ready
  - then write the result so I can carry it back

Back on this computer:
  - fold the result into the same document, tell me what conflicts with my own
    corrections if any, and bring the search index up to date for it

Explain what will change at each step before doing it, and tell me how much the
model calls are likely to cost.
```

### What comes back

- The **same document**: same identity, same page addresses, same citations. It
  is an update, not an import of a second copy. The two machines recognise the
  document by its **content** (and where it sits in your archive), not by a
  catalogue number, so the work lands on the right one.
- **Your own corrections are kept.** If you had corrected a page by hand here and
  the other machine read the same page, your correction wins and the difference
  is reported to you rather than silently overwritten.
- **A warning if the settings differ.** If the other machine read the document
  with a different palaeographer or editor than the one recorded here, pha says
  so ("stale") instead of pretending the result matches your choices.
- The **search index work is done here, once**, when the result arrives — not on
  both machines, which would double the waiting.

### Practical notes

- **Nothing needs to stay connected** between the two hand-over moments. The
  second machine can process for hours while your computer is asleep, and the
  other way round.
- **The document is on loan meanwhile.** Your pha lists it as out and skips it.
  You can cancel the loan at any time: that releases the document here and
  refuses the result if it arrives later.
- **Page images are not shipped.** The second machine makes its own from the
  document; your computer rebuilds any it is missing when the result returns.

### How this differs from simply giving documents away

pha can also **copy** a collection to another archive, with new identities —
useful for sharing or archiving. That is a gift: the copy lives its own life
there. A hand-over is the opposite: the document never leaves home, and what
returns is *work done on it*, merged back into the original.

---

## Which should I use?

- **Both computers usually on, same network, and you want to work with the
  archive as you go** → working together (scenario 1).
- **The powerful machine is elsewhere, always on, or not reachable from your
  computer** → taking turns (scenario 2).
- **You only need the models occasionally, and you would rather not keep another
  computer switched on** → taking turns.
- **You are processing one large collection and want it done quickly, with both
  machines available** → working together.

Either way, describe what you want and let your agent choose the mechanics. Both
are normal pha workflows, and both are reversible: in the first, you only change
where the models are reached; in the second, the document stays yours.

## The few technical words you will meet

| Word | What it means here |
|---|---|
| **model server** | the program on the powerful computer that makes its AI models available to the network |
| **serves the model** | which computer actually runs a given model — pha lets one job use each server at a time |
| **out on loan** | this computer has handed a document over and will not process it until it comes back |
| **ssh** | logging in to another computer from this one. You never need it here: each computer runs its own commands, and only the folder travels. |
| **Tailscale** | a private network between your *own* computers, wherever they are: it makes a machine in another city behave like one on your home network, with nothing opened to the public internet and no passwords to exchange. pha can use it to send and receive a hand-over folder by itself. |
| **the payload** | the folder that travels: the document going out, or the finished work coming back |
| **stale** | the work came back, but it was done with different settings than the ones recorded here |
| **embedding model** | the small model that powers searching *by meaning*; without it, keyword search still works |

---

# Operator reference

Everything above is written for the archive owner. This half is the
operator-level detail an **agent** needs when it is actually driving a two-machine
setup: the exact command surface, what travels, what is locked, and where the
tool lives on this machine. Read the part you need; `AGENTS.md` keeps only the
rules you must not get wrong.

## A. Moving collections between archives without redoing scan/edit (`bundle`)

`pha bundle <collection-or-doc...>` packs documents + finished library
transcriptions/edits/records + renders + the model definitions used into a
portable directory.

`pha unbundle <bundle>` (run on the target archive) imports it with NEW ids into
a populated archive, pins the recorded palaeographer/editor via selection files
(so `pha scan`/`pha edit` skip the imported docs instead of re-running them), and
indexes it for search.

`pha bundle --move` additionally DELETES the bundled documents from the source
archive once the bundle is written (the bundle is the backup).

See the README section "Moving / sharing collections between archives".

## B. Lending a document to a second machine (`handoff`)

`pha handoff` is the other shape from `bundle` — the document stays in its own
archive (same id, no copy) and a second machine does the slow scan/edit/encode.

### Owner side

`pha handoff out <target> [--worker NAME]` writes a payload and **leases** the
document, so `pha scan`/`edit`/`encode`/`reindex` skip it (name+age shown,
`pha status` lists it under "out on hand-over"; `--include-leased` overrides).

A document that has never been scanned can be handed out too: `handoff out`
**registers** it first (identity, resolved stages, page count — no render, no
transcription), and that record is what the lease is looked up through.

An `inbox/<rel>` target is moved into the dropbox first (mirroring the layout;
only entries you name, refusing to overwrite unless `--force-inbox`).

Renders are NOT carried either way (the worker re-renders to extract), so
`handoff fetch` rebuilds the page images missing here — a document handed out
before its first scan has none — and `pha render [--path … | --doc N]` does the
same on demand. Renders are a derived cache, so byte-identity across machines is
deliberately not required.

### Worker side

`pha handoff in <payload>`, then `pha handoff work --pages 12,337 --resume` (one
command for the whole job; `--resume` is STATELESS — it plans from the worker's
DB, so an interrupted page list continues and a finished document is skipped;
without it a named page is a deliberate re-do), then `pha handoff back`.

The explicit stages — `pha scan/edit/encode --path <doc>` — still work and report
the same.

### Back on the owner

`pha handoff fetch <result>` merges the work into the SAME document — local human
corrections are kept and reported as conflicts, and a document processed under a
different palaeographer/editor is reported `stale`.

Identity across machines is `sha256` + dropbox-relative path (ids are
per-machine and mean nothing across them); only the finished work travels back,
never the source bytes.

Leases live in `<archive>/.pha/handoffs/<id>.json` (machine-local, gitignored).
`pha handoff status [--json]` / MCP `pha_handoff_status()` show what is out;
`pha handoff cancel <id>` releases it and refuses a later result.

### What the worker keeps — there is no automatic clean-up

A hand-over is a loan with a **working copy**, not a transfer. `handoff back`
reads the worker's DB and writes the result folder; it **deletes nothing**. After
the result has gone home the worker still holds the source bytes it copied into
its own dropbox, the document's pages with their provenance, any edits and
encoder records, the library folder it wrote, its renders, the hand-out folder
and the result folder under `.pha/handoffs/incoming/<id>/`, and the incoming
marker `.pha/handoffs/<id>.json` — `state: "in"`, which `back` never changes;
only `pha handoff cancel <id>` releases it. Expect a full second copy on the
worker's disk, the sources and renders being the bulky part.

That marker is also what keeps the worker from embedding those documents
(`index deferred — received for hand-over`, gap G1), so a worker that is never
cleared keeps suppressing that index.

To reclaim the space, **once the owner has applied the result**
(`pha handoff fetch`):

1. `pha handoff cancel <id>` — releases the incoming marker.
2. `pha rm <document>` — **never** a shell `rm`/`mv`; it moves the dropbox
   payload and the library folder to `bin/<stamp>/` and clears the index
   (reversible with `pha bin restore`; `--purge` deletes for good).
3. Delete the leftover `.pha/handoffs/incoming/<id>/` and `<id>-back/` folders
   by hand — pha does not clean those either.

Do not clear the worker before the owner confirms the fetch applied: until then
the result folder may be the only copy of that work in flight.

### Moving the payload

**When the two machines share a tailnet, moving the payload is pha's job**:
`--send <peer>` on `out`/`back` tars the payload and Taildrops it
(`tailscale file cp`), and `pha handoff recv` on the other machine moves it out of
the Tailscale inbox, unpacks it into `<archive>/.pha/handoffs/incoming/<id>/` and
**prints** the `in`/`fetch` command — it never merges on its own (`--wait`;
`--loop` for a drop box; `--from PATH` for a file you copied yourself;
`--no-downloads` to ignore the macOS app's `~/Downloads`). No ssh keys, no shared
folder, no open port, no daemon: security is tailnet membership
(`pha handoff peers` lists who can receive). The tar is a **transport envelope
only** — the payload format is unchanged, and `handoff_transport.unpack` refuses
an absolute path, `..` or a link, because a tar from the network is untrusted even
on a tailnet.

Directory-moving (a shared folder, a USB stick, a zip) stays the fallback, and is
what to use when Tailscale is not on both machines.

**No path needs ssh**: every command runs on the machine that owns the archive it
touches, and moving the payload is a file copy. Do not propose ssh keys, remote
paths or a remote shell for a hand-over; `pha handoff out` prints the
copy-and-import next step, and this document explains the four ways to carry the
folder, in plain language for an archive owner.

### Unattended worker

`pha handoff worker --send-to <owner>` is the unattended trigger — the receiving
side of Taildrop is not a trigger, so on the always-on machine this watches the
inbox and runs `in` → `work --resume` → `back` → send for each hand-over. It
drives those as SUBPROCESSES of the documented CLI (never by importing the
pipeline), retries only `PeerUnavailable` (the owner's laptop is shut — that is
the normal case, so it waits; a misspelled/ambiguous peer is NOT retried
forever), and `--install` writes a user LaunchAgent (`~/Library/LaunchAgents`, no
admin) using the PATH-proof `<python> -m personal_historical_archive` form, so no
ssh is needed to trigger work at all. `--once`/`--status`/`--uninstall`/
`--dry-run`; log at `<archive>/.pha/handoff-worker.log`. It cannot wake a sleeping
LM Studio, so a missing model fails loudly in the log rather than hanging.

### Two locking/indexing gaps worth knowing

**`handoff fetch` applies without the embedding lock** (gap G3): merging rows,
rewriting library files and rebuilding renders touch no model, so the default
apply takes **no lock** (never refused by a running scan) and prints
`pha reindex --doc N …`; `fetch --index` embeds in the same command and takes the
lock (with `--wait` if asked). The result carries `indexed` and `index_doc_ids`
for agents.

**The worker does not embed hand-over material** (gap G1): `import_handoff`
records the document set here as RECEIVED (`state: "in"` — NOT a lease, so the
worker still scans/edits/encodes it), and `pha scan`/`pha edit` on the worker skip
the index for those documents, because the return payload carries text and
provenance but **no vectors** — the owner's `fetch` is the only place that embeds,
so embedding on both machines repeats the same hours of work (measured: 2,676
pages twice). `pha status` shows `index deferred — received for hand-over` instead
of `NOT INDEXED`, `pha handoff back` reports the documents returned without
chunks, and the escape hatches are `pha scan --index` / `pha edit --index` (one
run) or `pha handoff cancel <id>` (permanent).

## C. The DeepSeek Harness plugin (`dsh-pha`)

A Harness agent on a machine with pha + a Harness install
(<https://www.deepseek.com/en/harness/>) can expose the archive through the repo's
bundled `dsh-pha` plugin: it registers the `pha_*` model tools (`pha_status`,
`pha_documents`, `pha_document`, `pha_page`, `pha_search`, `pha_archive`,
`pha_job_start`/`pha_job_status`/`pha_job_kill`) and a same-origin `/pha/*` JSON
API. See [`DSH_PLUGIN.md`](DSH_PLUGIN.md) and
[`dsh-pha/README.md`](dsh-pha/README.md).

Install or update it with `pha view install --profile web` (omit `--profile` to
update the profiles that already carry the dsh-pha row). `pha update` runs this
step automatically after updating pha, so normally you only need to restart DSH
after an update.

Manual fallback: `pnpm add <repo>/dsh-pha` + the
[`cordis.patch` row](dsh-pha/cordis.patch.example.yml) + restart.

Reads are read-only (`immutable=1` sqlite / the `pha` CLI); **mutations still go
through the real `pha` CLI**, so the model-server lock, staleness and review
round-trip rules still apply — never start `pha scan`/`pha edit` while another
job holds the server they need.

## D. MCP across machines

The MCP server runs on the machine that owns the dropbox and models. A client on
another machine needs only an MCP connection — no local models or dropbox. Start
on the archive machine with `pha mcp --transport sse --host <LAN-IP> --port 8000`
(no auth — use a private LAN/VPN/SSH tunnel). See `MCP_CLIENTS.md` for the full
wiring.

## E. Where the `pha` tool lives on this machine

**`pha` not on PATH? Look in the archive first, don't guess.** Every pha run
leaves a machine-local trace of where the tool lives on THIS machine:

- **`<archive>/pha-location.md`** — visible; the archive's `README.md`/`AGENTS.md`
  point at it;
- **`<archive>/.pha/location.json`** — the machine-readable twin.

Both record the resolved executable, the version, the source checkout, the MCP
commands and — the part that always works — the PATH-proof interpreter form
`<python> -m personal_historical_archive`, which needs no PATH entry and no
activated venv and so works from any shell, cron job or agent runtime. Run it with
the archive passed explicitly:

```
PHA_ARCHIVE_DIR=<archive> <python> -m personal_historical_archive status
```

`pha info` prints the same tool fields (`pha_command`, `pha_version`,
`location_file`) from inside pha.

Only if that file is absent (pha has never run in this directory on this machine)
install it — `uv tool install --editable .` makes plain `pha` work in every shell.

The trace describes ONE machine (`hostname` + `written_at`): after a reinstall or
a move the recorded path may be stale, so fall back to the interpreter form rather
than trusting it blindly. It is gitignored — never commit it, and never let a
copied archive inherit it.

## F. Fresh install with no archive

**`pha` reports "No pha archive is configured or found"** — a fresh install with
no archive_dir set. Don't guess. Either point it at the real archive
(`pha set archive-dir <path>`) or create one (`pha init-archive ~/pha-home &&
pha set archive-dir ~/pha-home`). It stops rather than silently running against an
empty default DB.

## G. Uploading documents from another machine

`pha_upload(kind, name, content_b64)` (files travel as base64; single files per
call), then `pha_scan_now()`.
