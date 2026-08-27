# Agent Trace Studio

Standalone, browser-only viewer for local Agent run artifacts. It does not import or modify the production Runtime.

## Run locally

```bash
npm install
npm run dev
```

Open the printed local URL, then choose `trace.jsonl` and, when available, the matching `cost.json` with **Open run**. Parsing happens in the browser; files are not uploaded.

## Views

- **Economics** explains per-turn input, cache-read, uncached input, output, prefix epochs, context pressure and source-reading efficiency.
- **Lifecycle** maps Model, Plan, Permission, Tool, Verification and Context events on one causal timeline. Filters, failure focus, bookmarks and zoom are local UI state.
- **Trace** expands each model turn into its ordered runtime events. It focuses on gate decisions, failure evidence, recovery links, per-event timing, replay and raw forward-compatible fields without duplicating the other two views.
