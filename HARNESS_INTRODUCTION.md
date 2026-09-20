# Quick guide: how to get your harness

> Updated 18 September 2026

DeepSeek provides a low-cost LLM with vision capabilities on pre-paid credits —
no subscription. You can create separate API keys for the archive, which makes
the cost of reading manuscripts easy to see apart from the cost of running the
harness.

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

The quickest start is DeepSeek Harness Desktop (DSH Desktop): install from
[GitHub](https://github.com/anywhere-labs/dsh-desktop) or
[dshdesktop.cn](https://www.dshdesktop.cn/dsh/en/).

## Provide a API Key for the DeepSeek Harness
On first run it asks for a DeepSeek API key — give it `DSH_API_KEY`.

Once it starts, create a workspace for your archive with "Add workspace" (+)
near the top of the left pane.

`PHA_ARCHIVIST` will be used later if you need to define a remote model with
more capabilities than the local models on your computer.

If you decide to use DeepSeek online models for reading and editing documents,
you will be asked for that key at model setup time.

For now just store `PHA_ARCHIVIST` where you can get it back easily.

## Select the right model when interacting with harness

DeepSeek provides alternative models for handling your requests. The names may vary in the future, but currently three models are available:

1. DeepSeek_VNN_Flash - this is a fast model, low cost, use for generic work in the archive, like status, schedulling scans, making "normal" queries.
2. DeepSeek_VNN__Vision__ - this is a model that can "see". Use it when you query requires the model to read from your documents. It is usefull to read sample pages and then check the chepeast and fastest models for a specific document, by comparing its own reading with alternatives. When querying the archive a vision model can solve problems related to errors in reading by reading again selected pages and extract better readings.
3. DeepSeek_VNN_Pro - more capable and more expensive model, use for development of special filters to clean readding, or to make complex queries that the Flash model is not up to requirements. More expensive than Flash models and currently without vision.

## Next

Follow [HISTORIANS_README](HISTORIANS_README.md), then check that the archive
is reachable with `pha status`.
