# Enhancement — one model per model-server (endpoint-scoped locking)

**Status:** proposed — design draft, nothing implemented. Written against pha
**0.20.1**, 2026-09.
**Severity:** medium. The current rule is simultaneously **too coarse** (it
blocks job pairs that genuinely cannot collide — remote models) and **too
narrow** (two pha processes on one machine against two *different* archives take
two different locks and can load two models into the same LM Studio). Neither
half loses data on its own, but the narrow half re-opens the endpoint contention
that cost 13 885 embeddings in `pha-embed-loss-bug-report.md`.
**Motivating question:** *"the one local model rule could not apply when one of
the models is the embedding model, because it looks light on resources."*

## 1. Summary — the ask

State and enforce the rule as **one model per model-server**, not "one
local-model job at a time (per archive)". Two jobs may run concurrently **iff
the sets of servers they will talk to are disjoint**.

Deliverables:

1. **`server:` on the model interface** (`models/<id>.md`) — a declared identity
   for the machine/instance that serves the endpoint.
2. **A lock keyed on server identity**, held in a **user-global** lock directory
   (not inside the archive), so archives and `pha test` runs on one machine
   serialise against each other when they share a server — with a declared
   **capacity** per server (default 1), so a machine that pre-loads its models
   can admit more than one job.
3. **A preflight probe** of the endpoint's loaded instances, to catch the case
   where two different URLs are actually one device.
4. **Conservative defaults** — an unlabelled model file keeps today's global
   behaviour exactly.

The answer to the motivating question is *partly yes*: the embedding model is
genuinely small (LM Studio reports Nomic Embed at ~274 MB), so it does not
threaten RAM. But the rule does not exist to protect RAM, and **it is not an
LM Studio limit either** (§4): LM Studio caps nothing, and models you load by
hand coexist. It is pha's own conservative policy for the *default* server
configuration, in which Auto-Evict unloads JIT-loaded models — and because pha
never pre-loads, both of its stages are JIT loads, so the embed model does evict
the vision model — the churn this rule exists to prevent. (The bug report
attributes the incident's 120 s `embed()` timeout to endpoint contention rather
than to the eviction itself; the eviction-and-reload is a contributing cost, not
the proven cause.)
The exemption is therefore available on two axes — *a different server*, or *a
server with declared capacity* (§5.2) — but not *a smaller model*.

## 2. What the lock is today

```python
def _scan_lock_path(cfg: Config) -> Path:
    return cfg.data / "scan.lock"          # ingest.py:231
```

`cfg.data` **is the archive root** (`config.py:527`). The lock is one file per
archive, containing the owner pid, taken with `O_CREAT|O_EXCL`
(`ingest.py:273-308`) and reclaimed when the owner is dead or the file is older
than 6 h (`ingest.py:240-259`).

Who takes it:

| caller | takes lock at |
|---|---|
| `scan_once` | `ingest.py:2457` |
| `edit_all` | `ingest.py:1803` |
| `edit_documents_under` | `ingest.py:1837` |
| `reindex_all` | `ingest.py:2547` |
| `import_bundle` (`pha unbundle`) | `bundle.py:764` |
| `run_test` (`pha test`) | `testrun.py:684` |

What it is **not**:

- **Not per server.** Neither `pal.base_url` (`_vision_client`,
  `ingest.py:100-103`), the editor's model endpoint, nor `cfg.embed_base_url`
  (`ingest.py:875`) appears anywhere in the lock.
- **Not per machine.** Two archives side by side — this repo has
  `test-1577-archive/` next to the live archive — hold *different* locks and
  will happily load two models into the same LM Studio.
- **Not per model.** Two jobs that use the *same* model still conflict, which is
  correct (same slot), but for the wrong stated reason.

The refusal message ("one local model at a time", `ingest.py:2549`) describes a
machine-wide invariant that the mechanism does not implement; the enforcement
point is the archive.

## 3. Why the endpoint URL cannot identify the server

The intuitive implementation — resolve the host, treat loopback as local, treat
a LAN address as remote — is unsound, and LM Studio documents why. From
[Using LM Link](https://lmstudio.ai/docs/developer/core/lmlink):

> "With LM Link, you can use a model loaded on a remote device as if it were
> loaded locally — from any machine on the same link. […] your laptop can make
> requests to `localhost` and have them served by a powerful remote machine on
> your network. Requests to `localhost` still work as normal. LM Studio
> internally uses the model on the remote device as if it were loaded locally."

So `http://127.0.0.1:1234/v1` — the pha default — is **not** evidence that the
work happens on this machine. The GUI can show local vs remote because LM Studio
tracks devices internally; the REST surface pha talks to does not expose it.
[`GET /api/v1/models`](https://lmstudio.ai/docs/developer/rest/list) returns
`type` (`llm` | `embedding`), `publisher`, `key`, `quantization`, `size_bytes`,
`params_string`, `loaded_instances`, `max_context_length`, `format`,
`capabilities` — and **no device or provenance field**.

Further counterexamples to URL-based inference:

- **Tunnels** — `ssh -L 1234:remote:1234` presents a remote server on loopback.
- **Self via LAN IP** — the same box reached as `192.168.x.y:1234` instead of
  `127.0.0.1:1234` looks "remote" to a naive resolver, yet shares its RAM.
- **VPN / mDNS hostnames** — resolution is ambiguous and can change per run.

The usable asymmetry:

- *Same normalised endpoint ⇒ same instance ⇒ must serialise.* **Sound.**
- *Different endpoint ⇒ different machine ⇒ safe to parallelise.* **Unsound.**

Any design must therefore never conclude "safe" from the URL alone.

## 4. What the contention actually is

**Nobody enforces "one model at a time" — least of all LM Studio.** Models loaded
by hand in the LM Studio UI (or via `lms load`) coexist, and a machine with the
RAM can hold several; there is no cap in the API. What LM Studio does *by
default* is evict **JIT-loaded** models when a new model is JIT-loaded. From
[Idle TTL and Auto-Evict](https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict):

> **When Auto-Evict is ON** (default): At most `1` model is kept loaded in
> memory at a time (when loaded via JIT). **Non-JIT loaded models are not
> affected.**

> **When Auto-Evict is OFF:** Switching models from an external app will keep
> previous models loaded in memory.

**Where the switch is.** These behaviours are separate switches under **LM Studio
→ Developer tab (`</>`) → Server Settings** (labels per the
[Server Settings](https://lmstudio.ai/docs/developer/core/server/settings) page):

| switch | what it does | for pha |
|---|---|---|
| **Just in Time Model Loading** | loads models at request time | leave **ON** — pha never pre-loads, so with this off it fails with `Model '…' is not served` |
| **Auto Unload Unused JIT Models** | the Idle TTL: unload a JIT model once it goes unused (default 60 min) | orthogonal; the mitigation for the memory cost below |
| **Only Keep Last JIT Loaded Model** | the docs' **Auto-Evict**: unload the previous JIT model when a new one is loaded | **the switch this whole document is about** |

Turning *Only Keep Last JIT Loaded Model* **off** is the one-click version of the
capacity change in §5.2: pha's vision, editor and embed models then coexist, and
concurrent jobs stop evicting one another — bounded by RAM/VRAM instead, which is
exactly the pressure AGENTS.md's disk-fill warning describes. There is no API for
reading these settings, which is why §5.2 treats capacity as **declared** by the
operator rather than discovered.

**pha never pre-loads, and never unloads.** There is no call to
`POST /api/v1/load` or `/api/v1/unload` anywhere in the package; every request
goes through chat/embed and relies on JIT — the client exposes only
`list_models`, `chat_vision`, `chat_text` and `embed`, and hits just
`/chat/completions` and `/embeddings`. **LM Studio is the only actor that evicts
anything**; pha's sole lever is whether to *send* the request. So every model pha
loads is a JIT load, and against a default server pha's own two stages evict each
other by construction.

Consequences that shape the design:

1. **The "one model" rule is a pha policy about a default configuration, not a
   hardware constraint.** A prepared machine has more real capacity: pre-load the
   vision and embed models by hand (with no TTL, so auto-evict leaves them
   alone), and two jobs coexist without eviction, sharing only CPU/GPU
   throughput. The design that follows is **declared capacity** (§5.2) with a
   default of 1 — not a permanent binary lock.
2. **Weight is still the wrong axis.** Loading a 274 MB embed model evicts a
   multi-GB vision model; the cost is the *displacement* (reload, and the
   page-out to disk AGENTS.md warns fills the disk), not the small model's
   footprint. Two jobs alternating vision and embed requests can evict each other
   on *every request* — the churn this rule exists to prevent, and a plausible
   contributor to the timeout in `pha-embed-loss-bug-report.md` (which attributes
   it to endpoint contention).
3. **The contended unit is still the serving device**, because eviction happens
   where the models live — exactly what §3 says the URL cannot tell us. And the
   resident set is not static: TTL defaults to 60 min and resets per request, so
   a vision model left idle behind a long reindex can expire on its own.

Observability this feature can lean on: `lms ps --json` (loaded models and queued
requests; API equivalent `loaded_instances` in `GET /api/v1/models`),
`lms load --estimate-only` (a memory estimate — how to make "looks light" a
measurement), and the key inference that a model **already resident** means the
request loads nothing and therefore cannot evict anything.

## 5. Design

### 5.1 `server:` — declared identity on the model interface

Add one key to `models/<id>.md` front matter:

```yaml
---
description: Local Qwen3-VL 8B (LM Studio)
server: mac-studio          # <- new; a device/instance id of your choosing
base_url: http://127.0.0.1:1234/v1
model: qwen/qwen3-vl-8b
---
```

- **Backwards compatible by construction.** Model front matter is parsed with
  `fm.get(...)` and no strict-key validation (`_model_from_frontmatter`,
  `config.py:862-885`; legacy inline path `config.py:905-925`), so an older pha
  ignores the key and a newer pha reads it from files that already exist.
- **Free-form, user-declared.** The right value is whatever the GUI calls the
  device. It is deliberately *not* derived, because §3.
- **Per model file, not per stage** — an editor on a different box from the
  palaeographer is normal and per-file modelling expresses it naturally.
- **A remote API with no LM Studio slot** (`api_style: anthropic`, a
  MiniMax-style endpoint) still gets a `server:` — the point is an identity to
  serialise on, not a hardware claim. This is what lets AGENTS.md's existing
  observation ("remote models don't compete with LM Studio") become mechanical.

### 5.2 The lock, keyed on server identity, in a user-global directory

Move the lock out of the archive and key it:

- **Key derivation**, in order: declared `server:` → the normalised endpoint
  (`scheme://host:port`, after expanding `${ENV}`) → **the wildcard `*`**.
- **The fallback is the wildcard, not the URL.** An unlabelled model file makes
  its job take `*`, which intersects every other key set — i.e. today's global
  behaviour, preserved bit for bit. This is the deliberate consequence of §3:
  *unknown provenance ⇒ assume shared.*
- **Lock directory is user-global**: `~/Library/Caches/pha/locks/` on macOS,
  `$XDG_RUNTIME_DIR/pha/locks/` (else `~/.cache/pha/locks/`) elsewhere,
  `%LOCALAPPDATA%\pha\locks\` on Windows. File name
  `sha1(key)[:12] + ".lock"`, same pid content and stale-reclaim logic as today.
- **A job holds a set of keys**, one per distinct server it may touch
  (palaeographer + editor + embed endpoints, plus `*` when anything is
  unlabelled). Conflict iff two key sets intersect. Acquire in **sorted order**
  and release everything on refusal, so two jobs needing `{A,B}` and `{B,A}`
  cannot deadlock.
- **Capacity, not a binary.** A server admits up to its declared number of
  concurrent jobs, default **1**. Declare it once per machine, in `config.yaml`,
  since several model files share one server:

  ```yaml
  servers:
    mac-studio: {slots: 2}    # vision + embed pre-loaded by hand (§4)
    local:      {slots: 1}    # also the default for anything undeclared
  ```

  This is the knob that makes the rule honest: a machine with *Only Keep Last JIT
  Loaded Model* off (§4), or with the needed models loaded by hand, genuinely
  supports two jobs and can say so; a default JIT machine keeps `1`. It costs nothing to implement because it
  reuses the existing atomicity trick — `slots: N` means N lock files per key
  (`<hash>.1.lock` … `<hash>.N.lock`), take the lowest free one with
  `O_CREAT|O_EXCL`, so there is no read-modify-write and no new race.
- **A resident model needs no slot.** Once the resident-set probe (§5.3) is in
  place, a job whose models are *already loaded* can be admitted even at
  capacity: its requests load nothing and therefore evict nothing. This is the
  precise form of the observation that manually loaded models are exempt from
  auto-evict.
- **Keep the archive-local lock too, or not?** Either works once keys exist; the
  cleanest is to replace `scan.lock` with server keys, since a server key
  already covers same-archive contention. The archive file can stay as a
  migration courtesy (stale-reclaim it after one release) to avoid two pha
  versions disagreeing during an upgrade.
- **The refusal message should name the busy server** and the holder, e.g.
  `server 'mac-studio' is busy (pha scan, pid 1234)` — today's text names
  neither the server nor the job.

### 5.3 Preflight probe — the only authoritative signal

Before acquiring, optionally `GET /api/v1/models` on each endpoint the job will
use and read `loaded_instances`:

- A model already resident that this job will *not* use ⇒ **warn** (or refuse
  without `--force`): the capacity is taken, whether or not the config calls the
  servers different. Conversely, a model the job needs and finds **already
  resident** ⇒ admit it even at capacity (§5.2), because nothing will be loaded
  and nothing evicted.
- This is what covers the LM Link and tunnel cases from §3, where two distinct
  URLs are one device.
- **Caveats, which is why it is a preflight and not the mechanism:** it is a
  snapshot (TOCTOU — the state can change mid-run), it needs an API token when
  authentication is enabled, and under auto-evict a resident model is not an
  error but a *cost*. Treat it as advisory by default.
- **Optional auto-discovery:** if two endpoints report the same loaded-instance
  `id`, they are the same device. That could wire `server:` values automatically
  (or at least report "these two model files share a device but are labelled
  differently") from `pha doctor`. Nice-to-have; not required.

### 5.4 Reporting and seeding

- `pha doctor` reports model files sharing one key, and every unlabelled file
  (i.e. "these serialise globally"), so the labelling state is visible without
  reading config.
- Do **not** auto-write `server:` into existing model files; the value encodes
  a fact about hardware that pha cannot observe (§3). Ship a documented default
  in the samples instead.

## 6. Gaps this fixes beyond the motivating question

### 6.1 Cross-archive collisions (a real bug today)

`test-1577-archive/` beside the live archive, or two archives on one laptop,
take different locks and can load two models into one LM Studio. The stated rule
is machine-wide; the implementation is archive-wide. Server-keyed locks in a
user-global directory close this.

### 6.2 `pha search` embeds the query with no lock (two of three search surfaces)

There are three search entry points; only two touch the embed model:

| surface | mode | embeds the query? | lock |
|---|---|---|---|
| `pha search` — `cmd_search`, `cli.py:74` | `cfg.default_mode` = hybrid | **yes** | **none** |
| FastMCP `pha_search` — `mcp_server.py:47` | `mode="hybrid"` default | **yes** | **none** |
| dsh-pha `/pha/search` — `dsh-pha/lib/index.js:568` | **hard-coded `--mode keyword`** | no | n/a |

So the plugin route an agent in this repo actually gets is already safe — by
conservatism, not by design. The offenders are the CLI and the FastMCP tool, and
a search during a scan loads the embed model and evicts the vision model
mid-page. This is the "the embed model is light, so it doesn't count" assumption
**already shipped in code**, in the one place nobody looked.

**The fallback already exists, and it fires too late.** `search()` already
returns `{mode, query, results, note}` (`search.py:103-134`); `_embed_query`
already swallows `ModelError` (`search.py:16-17`); hybrid already degrades to
keyword-only with a note, and `cmd_search` already prints it (`cli.py:112`). The
defect is the **trigger**: the degrade happens only *after* the embed is
attempted — that is, after the load has already evicted the vision model. Search
buys its keyword fallback at exactly the price the fallback exists to avoid.

So the fix is small and reuses what is there:

1. **Decide before the call.** In `search()`, if `mode != "keyword"` and a
   local-model job holds the lock, skip `semantic_search` entirely (`sem = []`)
   and set an actionable note. `_embed_query` is never reached, so **no model is
   loaded and nothing is evicted**; keyword hits over the same corpus still
   answer the query.
2. **Search observes the lock; it never takes it.** Taking it would either block
   an interactive query for the length of a two-hour scan, or make `pha scan`
   refuse because a search was in flight. Observation is the right shape for a
   read-only command, and it fixes both call sites at once, because both go
   through `search()`.
3. **Reuse the staleness logic.** `_scan_lock_held_by_other(cfg)`
   (`ingest.py:262`) already ignores dead pids and >6 h locks, so a crashed scan
   cannot downgrade search forever. It is private and lives in `ingest`; move it
   into the `locks.py` module from §5.2 and import it from `search.py`. No cycle:
   `ingest` does not import `search` today, and a thin search layer importing the
   whole ingest machinery would be the wrong direction anyway.
4. **Surface the note on every surface.** The CLI already does. The FastMCP tool
   throws it away — `return res["results"]` (`mcp_server.py:47`) — which today
   already discards the *existing* "embedding model unreachable" note, so an MCP
   agent cannot tell hybrid results from keyword-only ones. Recommend returning
   the same `{mode, query, results, note}` shape as `pha search --json`; the
   inconsistency between the two surfaces is itself the bug, and MCP transports
   arbitrary JSON. This *is* a contract change (`MCP_CLIENTS.md:13`, and the
   notes-search plan assumes pass-through) — a non-breaking alternative is a
   uniform `note` key on every hit (§8.3).
5. **Explicit `semantic` keeps its honest behaviour** — empty results plus the
   "semantic unavailable" note (`search.py:123-127`), with the note now naming
   the running job — and a `--force` / `--allow-embed` escape hatch lets a user
   who has a separate embed server, or who accepts the eviction, override it.
6. **The real fix is a second server.** If `embed_base_url` points at its own
   instance (a separate llama.cpp/Ollama process, or another box), search and
   scan are genuinely disjoint and hybrid search stays live during ingestion —
   which matters most here, where an agent drives searching *and* ingesting. With
   §5's `server:` labels that setup is *declared*, so the degrade never runs. The
   degrade is correct behaviour for the single-server default; a dedicated embed
   server is the right configuration for an archive that searches while it
   ingests.
7. **Optional refinement.** Probe `loaded_instances` (§5.3) and skip only when the
   embed model is *not* already resident. If it is resident — loaded non-JIT via
   `lms load`, auto-evict disabled, or TTL not yet expired — the query costs no
   eviction and should proceed.
8. **The residual TOCTOU race is acceptable.** A scan starting between the check
   and the embed costs at most one eviction and one vision-model reload; because
   `index_document` now computes before destroying, it cannot cost vectors —
   worst case a page fails and is retried. Don't add machinery to close it.

**Minimal patch — this is a handful of lines, not a subsystem.** The existing
`note` field carries the warning, so the CLI needs no change at all:

```python
# search.py, in search(), replacing the bare `sem = semantic_search(...)`
sem: list[dict] = []
note = None
if locks.job_running(cfg, embed_key(cfg)):                       # NEW
    note = ("A local-model job (scan/edit/reindex) is running; semantic search "
            "skipped so it keeps its model loaded. Keyword results only — "
            "re-run when it finishes, or use a separate embed server.")
else:
    sem = semantic_search(conn, client, cfg.embed_model, query, limit, collection)
```

Everything after it is unchanged, except that the two existing note assignments
(`search.py:126`, `132`) must not overwrite the new one. `cmd_search` already
prints `res["note"]` (`cli.py:112`); no lock is taken, no new failure mode is
introduced, and `ingest.py` is not touched. Only the FastMCP surface still needs
a decision (§8.3) — and note that it discards the note **today** as well, for the
pre-existing "endpoint unreachable" degrade.

The one thing to get right is the *scope of the question*. Today the only lock
is per archive, so "is any pha job running?" and "is the embed server busy?" are
the same question and the simple check is correct. Once §5 lands they diverge: a
scan running against a **remote** palaeographer server does not occupy a local
embed server, and degrading search for it would be needless. Asking the lock for
the *embed server's* key (`locks.job_running(cfg, embed_key(cfg))`) rather than
for "any job" keeps the simple rule correct through that change.

Severity is bounded and worth stating plainly: this costs model reloads (and the
disk paging behind them) per query, not data, and one of the three surfaces was
already safe. It is a cheap fix with a disproportionate payoff, not an incident.

### 6.3 `embed_backend` is declared but never read

`config.py:413` declares `embed_backend`, `config.py:543` sets it (default
`"lmstudio"`), and **nothing branches on it** — every embed call path uses
`cfg.embed_base_url` unconditionally (`ingest.py:875`, `cli.py:74`,
`mcp_server.py:47`, `bundle.py:769`). It is the natural home for a precise,
declared exemption (`backend: remote` ⇒ different server by definition), and it
is currently dead config.

## 7. Alternatives considered and rejected

| alternative | why not |
|---|---|
| **Keep the global-per-archive lock** | Blocks the remote-model parallelism AGENTS.md already documents as safe, and does not actually cover the machine (§6.1). |
| **Infer local vs remote from the URL** (loopback test, interface-address comparison, `bind()` probe) | §3: LM Link makes `localhost` remote, tunnels make remote look local, a LAN IP can be self. Sound only in the same-endpoint direction. |
| **Judge by model weight** (`size_bytes`, `lms load --estimate-only`) | §4.2: the cost is evicting the *large* model, not the small one's footprint. The embed model being light is exactly why it is dangerous — it is cheap to load, so it is loaded freely. |
| **Query the endpoint before every request and serialise dynamically** | Correct but expensive (a probe per request), still TOCTOU, and needs auth. Kept as the §5.3 preflight instead. |
| **Treat "one model at a time" as an LM Studio invariant** | §4: it is not one. LM Studio caps nothing, and models loaded by hand coexist (its default Auto-Evict only unloads *JIT-loaded* models, and pha never pre-loads). The rule is pha's policy about a default configuration, which is why §5.2 makes capacity declarable instead of assuming 1 forever. |
| **Lock per model id rather than per server** | The contended resource is the instance's JIT slot, not the model: two *different* models on one instance conflict. |
| **Trust the GUI / a user-maintained list in `config.yaml`** | A list of *known* servers is fine as a default, but the pairing of model → server belongs on the model interface, which is where endpoints already live. |

## 8. Open questions

1. **Naming and scope of the key.** `server:` vs `device:` vs `host:`. Does it
   ever need to be a *list* (one model reachable on two devices)?
2. **Auto-discovery** from loaded-instance ids (§5.3) — build it, or just report
   conflicts in `pha doctor`?
3. **MCP result shape (§6.2–4).** Make FastMCP `pha_search` return
   `{mode, query, results, note}` (matching `pha search --json`), or keep the
   list and add a uniform `note` key per hit? The former is cleaner and is a
   contract change for MCP clients; the latter is non-breaking but muddier.
   The *degrade* decision itself is settled — keyword-only, decided before the
   embed call — so this is only about how the note reaches the client.
4. **Wildcard visibility.** Warn on every run that touches an unlabelled model,
   or only in `pha doctor`? (Warning every run would be noisy in exactly the
   setups that have not migrated.)
5. **Migration.** Delete `scan.lock` immediately, or keep it for one release?
6. **Does `pha test` need the same granularity?** It is short-lived but does load
   models; today it shares the archive lock (`testrun.py:684`).
7. **Capacity (§5.2).** Is `servers: {<id>: {slots: N}}` in `config.yaml` the
   right home (vs. a key on the model file)? And should pha *verify* a declared
   capacity rather than trust it — e.g. warn when a job at capacity would still
   evict a resident model it needs, or refuse a `slots: 2` declaration when the
   jobs in question have no models pre-loaded? The §5.3 probe makes verification
   possible; the question is whether it is worth the coupling.

## 9. Implementation sketch

- `config.py` — `server: str = ""` on `Model` (~line 172) and its copies
  (`Palaeographer`, `Editor`, …, see the `common` construction at line ~597);
  read `fm.get("server", "")` in both parse paths (`config.py:864`, `905`).
- New `src/personal_historical_archive/locks.py` — `server_key(model_or_url)`,
  `lock_dir()`, `slot_path(key, i)`, `capacity(cfg, key)` (default 1),
  `acquire(keys) -> bool`, `release(keys)`, `at_capacity(cfg, key)`; `slots: N`
  is N lock files per key, so atomicity is unchanged. Move the existing
  `_scan_lock_*` bodies there and leave thin wrappers in `ingest.py` so
  `bundle.py` / `testrun.py` imports keep working.
- `config.py` — parse a top-level `servers:` block (`{<id>: {slots: N}}`,
  default 1) and expose it on `Config`; validate `slots` is a positive int.
- Compute a job's keys where the clients are already resolved — for scans that
  is the per-file loop in `scan_once` (`ingest.py:2480-2521`), which already
  builds `_client_key(pal)`; collect the distinct `server:` values before
  taking the lock rather than during.
- Refresh the refusal text at all five copies — `ingest.py:1805`, `1839`,
  `2549`, `bundle.py:766`, `testrun.py:685` — to name the busy server and the
  holding job/pid. Better still, have the lock return a structured reason and
  format it once at the call sites.
- Propagate `server` into the `common` override dict in `resolve_model`
  (`config.py:596-600`), or resolved stages will lose the label the lock reads.
- `search.py` (§6.2) — before calling `semantic_search`, consult
  `locks.job_running(cfg)`; when a job holds the lock and `mode != "keyword"`,
  skip the embed and set the note. Add `--force` / `--allow-embed` to
  `cmd_search` (`cli.py`). `mcp_server.py:47` returns the full result dict
  instead of `res["results"]`, per §8.3.
- Tests to add: a held lock makes hybrid search return keyword hits with a note
  and **never constructs an embed request** (assert the client is untouched or
  spy on `_embed_query`); keyword mode is unaffected by the lock; a stale/dead
  lock does not degrade; explicit `semantic` returns empty + note; `--force`
  attempts the embed anyway.
- Tests to add: two archives with disjoint `server:` values run concurrently;
  the same `server:` on two archives refuses; an unlabelled file refuses against
  everything; two jobs needing `{A,B}` acquire in a stable order; stale-reclaim
  and dead-pid reclaim still work from the new directory; `server_key`
  normalisation (`127.0.0.1` vs `localhost`, trailing slash, `${ENV}` expansion).
- Docs: AGENTS.md's "Only ONE local-model job at a time" bullet becomes "one
  model per **model-server**"; the `_sample.local-*.md` model files gain a
  commented `server:` line.

## 10. Non-goals

- Not a job queue or scheduler; the existing refuse-and-rerun behaviour stays.
- Not resource-aware admission control (free RAM/VRAM estimation).
- Not a change to review/staleness semantics, or to what `--reprocess` means.
- Not a change to LM Studio's own TTL / auto-evict settings — pha should work
  correctly under the defaults, and the docs are cited here only to explain why
  the lock exists.

## 11. Relation to the existing docs

- `pha-embed-loss-bug-report.md` fixed the **consequence** of overlap (a failed
  embed can no longer destroy vectors; `pha reindex` now takes the lock). This
  proposal addresses the **cause**: the lock's granularity, and the unlocked
  embed paths in §6.2 that the bug fix did not cover.
- `FILTERS_PLAN.md` §4/§8 assume re-running a stage is safe; the same assumption
  is what makes a *correct* concurrency rule worth having, since re-runs are
  routine and a needlessly global lock makes them needlessly serial.
- AGENTS.md states the operational rule; this doc is the design for enforcing it
  where it is actually true.

## References

- [Using LM Link](https://lmstudio.ai/docs/developer/core/lmlink) — `localhost`
  requests may be served by a remote device.
- [Idle TTL and Auto-Evict](https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict)
  — at most one JIT-loaded model per instance; non-JIT models unaffected.
- [List your models (`GET /api/v1/models`)](https://lmstudio.ai/docs/developer/rest/list)
  — `loaded_instances`, `type`, `size_bytes`; no device/provenance field.
- [API changelog](https://lmstudio.ai/docs/developer/api-changelog) —
  `lms ps --json` (loaded models + queued requests), `lms load --estimate-only`
  (memory estimate).
