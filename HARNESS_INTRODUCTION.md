# Quick guide: how to get your harness

> Updated 22 October 2026

DeepSeek provides a low-cost LLM with vision capabilities on pre-paid
credits — no subscription. You can create separate API keys for the archive,
which makes the cost of reading manuscripts easy to tell apart from the cost
of running the harness.

DeepSeek Harness is also customisable, which lets the archive manage its own
user interface: a GUI for browsing documents, reading pages and moving parked
items.

## Get a DeepSeek account

1. Go to [platform.deepseek.com](https://platform.deepseek.com/) and create an
   account.
2. Under "Usage" (left-hand menu), buy some credits. 10 USD supports
   significant testing of an archive.
3. Under "API keys" (left-hand menu), create **two** keys and save both
   strings:

   - `DSH_API_KEY` — for managing the harness
   - `PHA_ARCHIVIST` — for reading and editing documents

   The two names are **labels in the DeepSeek dashboard**, so you can see later
   which usage came from the harness and which from reading and editing
   documents. Nothing else depends on what you call them.

## Get DeepSeek Harness

Download DeepSeek Harness Desktop (DSH Desktop) from the official page —
<https://www.deepseek.com/en/harness/>. **Download Desktop** there gives the
installer for your computer; the same page explains what the Harness is and
what its plugins do.

## Provide an API key for DeepSeek Harness

On first run it asks for a DeepSeek API key — give it `DSH_API_KEY`.

Once it starts, create a workspace for your archive with "Add workspace" (+)
near the top of the left pane.

`PHA_ARCHIVIST` will be used later if you need to define a remote model with
more capabilities than the local models on your computer.

If you decide to use DeepSeek online models for reading and editing documents,
you will be asked for that key at model setup time.

For now just store `PHA_ARCHIVIST` where you can get it back easily.

## Select the right model when interacting with the harness

DeepSeek provides alternative models for handling your requests. The names may
vary in the future, but currently two models are available:

1. DeepSeek_VNN_Flash — a fast, low-cost model, with vision capabilities. Use
   it for generic work in the archive, such as status checks, scheduling scans
   and "normal" queries. It is also what you use to read sample pages and then
   choose the cheapest and fastest model for a specific document, by comparing
   its own reading with the alternatives. When querying the archive, a vision
   model can also solve reading errors by reading selected pages again and
   extracting better readings.
2. DeepSeek_VNN_Pro — a more capable, more expensive model; use it to develop
   special filters that clean up readings, or for complex queries that the
   Flash model cannot handle. More expensive than the Flash model and
   currently without vision.

## Get the archive's own window (the PHA view)

pha ships a plugin that puts the archive itself inside the harness: a **PHA**
tab beside the conversation, with the document list and its search, a page
reader showing the transcription and the edited version, the page image side by
side, the documents parked in the inbox, and each document's bibliographic
reference. It only reads — anything that changes the archive still goes through
the agent and the normal commands.

Install it on the machine that holds the archive, then restart the harness:

```sh
pha view install          # the profile the app runs (DSH Desktop: web)
pha view status           # installed? which profile?
```

The **PHA** tab then appears in the conversation header. If it does not, the
usual cause is a profile mismatch: pass `--profile` with the one the app
actually runs. `pha help view` prints the full plugin guide, which ships with
pha.

## Next

Follow [HISTORIANS_README](HISTORIANS_README.md), then check that the archive
is reachable with `pha status`.

This guide also ships with pha: `pha help harness` prints the path of the copy
in this installation — the archive's, the checkout's, or the installed
package's.
