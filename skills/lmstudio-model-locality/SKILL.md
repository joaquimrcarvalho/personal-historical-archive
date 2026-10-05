---
name: lmstudio-model-locality
description: Use when configuring or starting `pha scan` / `pha edit` models on a machine with LM Studio and LM Link, especially when a model may be loaded on another device. Determines whether each model copy is local or remote with the `lms` CLI (the LM Studio API does not expose locality), and gives the pre-load recipe that makes a local model immune to LM Link routing.
---

# LM Studio Model Locality (LM Link)

## Why this exists

pha talks to an OpenAI-compatible endpoint (`base_url`, usually
`http://127.0.0.1:1234/v1`) and names a model (`model:` in
`models/<id>.md`). With **LM Link**, that local endpoint can be answered by
another device: the local LM Studio keeps serving the same port and routes a
request for a model that is already loaded remotely to that remote instance,
even when a copy exists on the local disk but is not loaded.

The LM Studio API does **not** tell you where a model is:

- `GET /v1/models` lists ids, nothing about location;
- `GET /api/v0/models` adds `state: loaded|not-loaded`, but still no device;
- an id shown as `loaded` can be loaded on a different machine.

The `lms` CLI does expose location. Always decide with `lms`, not with the
API, when the machine has LM Link enabled (or when you are not sure).

## The locality rules

Run the helper from this skill folder:

```sh
python3 scripts/lmstudio_locality.py                # all models
python3 scripts/lmstudio_locality.py --model qwen   # filter by id substring
python3 scripts/lmstudio_locality.py --json
python3 scripts/lmstudio_locality.py --model qwen --require-local
```

The rules it applies:

| view | command | local means | remote means |
| --- | --- | --- | --- |
| on disk | `lms ls --json` | `deviceIdentifier` is null / empty / this device's id | `deviceIdentifier` is another device's id (and `path` is often `"<deviceId>:..."`) |
| loaded | `lms ps --json` | `deviceIdentifier` is null / empty / this device's id | `deviceIdentifier` is another device's id |
| device names | `lms link status --json` | this device's `deviceIdentifier` / `deviceName` | each `peers[]` entry maps a remote id to a device name |
| server | `lms server status --json` | `running: true`, `port` | the local server is not running |

`lms ps --json` lists the loaded instances across LM Link; join
`deviceIdentifier` with `lms link status --json` to get the machine name.

Do not infer locality from:

- `base_url: http://127.0.0.1:1234/v1` — the port is local, the model is not;
- a `model:` id that appears in `/v1/models` — every LM Link device's models
  appear there too;
- `state: loaded` in `/api/v0/models` — loaded somewhere, not necessarily here.

## Preflight before `pha scan` / `pha edit`

1. Read the model the pass will use (for example
   `models/qwen3-vl-8b.md`) and note its `model:`, `base_url:` and `server:`.
2. Ask where that model is:
   ```sh
   python3 scripts/lmstudio_locality.py --model <model-id>
   ```
3. Act on the verdict:

   - **`local_loaded`** — ready. The helper prints the loaded identifier;
     make sure the model file's `model:` matches an identifier the API lists.
   - **`local_available_not_loaded`** — the usual LM Link trap. Pre-load the
     local copy *under a unique identifier* and point pha at that identifier:
     ```sh
     lms load <model-key> -y --identifier pha-local-<short-name>
     ```
     then in the pha model file:
     ```yaml
     model: pha-local-<short-name>
     ```
     `model:` must be the identifier, not the shared model key, so the request
     cannot be routed to the remote loaded instance. `pha`'s own
     `check_model` looks the identifier up in `/v1/models`, where a locally
     loaded instance appears immediately.
   - **`remote_loaded_only` / `remote_only`** — there is no local instance.
     Either download/pre-load a local copy and follow the step above, or
     accept remote execution explicitly. For remote execution set the model
     file's `server:` to the remote device name so pha's model-server lock
     serialises with other jobs that use that device, and budget for the
     network latency.
   - **`not_found`** — not on local disk and not visible through LM Link;
     download it or choose another model.

4. Re-run the helper with `--require-local` after loading:

   ```sh
   python3 scripts/lmstudio_locality.py --model <model-id> --require-local
   ```

   Exit status 0 means every matched model has a locally loaded instance.

5. Then start the pass. `pha doctor` shows the server keys and slots that
   pha will lock; see `skills/pha-document-operations/SKILL.md` for the
   targeted scan/edit commands and the model-server lock rules.

## The deterministic local recipe, in full

This was verified against LM Studio with LM Link, including a remote instance
of the same model already loaded:

```sh
lms load text-embedding-nomic-embed-text-v1.5@q4_k_m \
  -y --identifier pha-local-embed

# observe: the local instance has deviceIdentifier null
lms ps --json

# the API lists the identifier, and requests using it are served locally
curl -s http://127.0.0.1:1234/v1/models
curl -s http://127.0.0.1:1234/v1/embeddings \
  -H 'content-type: application/json' \
  -d '{"model":"pha-local-embed","input":"locality probe"}'
```

Set the pha model file accordingly:

```yaml
# models/embed-local.md
base_url: http://127.0.0.1:1234/v1
model: pha-local-embed
server: this-machine
```

The unique identifier is the important part: the shared model key can already
be loaded remotely, while a fresh `pha-local-*` identifier is a local instance
and nothing else.

## If you cannot pre-load: disable LM Link for the pass

If the local model cannot be pre-loaded reliably, disable LM Link while the
pass runs, so the local server can only resolve local models:

```sh
lms link disable
# run the pha scan/edit pass
lms link enable
```

This is deterministic but machine-wide: it also stops other people on this
machine from reaching remote models while the pass runs. Prefer the unique
identifier recipe unless remote routing must be impossible.

## Quick reference

```sh
lms ls --json                # copies on disk; null deviceIdentifier = local
lms ps --json                # loaded instances; null deviceIdentifier = local
lms link status --json       # device id -> name, peers, this device
lms server status --json     # local server running/port
lms load <key> -y --identifier pha-local-<name>   # force a local instance
lms unload <identifier>                            # remove an instance
```

`lms load` is a mutation: load only the model you are about to use, and unload
it when the pass is over if the machine needs the memory. Never unload or
restart another user's loaded model without being asked.
