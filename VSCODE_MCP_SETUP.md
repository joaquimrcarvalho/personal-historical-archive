# Chatting with your pha archive from VS Code (zero-code setup)

pha ships an MCP server (`pha mcp`) with the full set of `pha_*` tools —
search, read documents/pages, collection config/status, trigger scans, check
the schema, run doctor. VS Code has a built-in MCP client, so **no extension
is needed** to talk to your archive from Copilot chat.

## Local (VS Code on the archive machine)

Create `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "pha": {
      "type": "stdio",
      "command": "pha",
      "args": ["mcp"]
    }
  }
}
```

If `pha` is not on PATH, use the full venv path, e.g.
`"command": "/path/to/personal-historical-archive/.venv/bin/pha"`.
If your archive dir is not auto-detected, add `"env": {"PHA_ARCHIVE_DIR": "/path/to/archive"}`.

## Remote (VS Code on another machine, archive on the LAN)

On the archive machine, start the server (no auth — private LAN/VPN/SSH
tunnel only):

```sh
pha mcp --transport sse --host <LAN-IP> --port 8000
```

Then in VS Code settings (user `mcp.json` or workspace `.vscode/mcp.json`):

```json
{
  "servers": {
    "pha-remote": {
      "type": "http",
      "url": "http://<LAN-IP>:8000/sse"
    }
  }
}
```

See `MCP_CLIENTS.md` for the full wiring, security notes, and the tool list.

## Then what?

Open Copilot chat in **agent** mode: the `pha_*` tools appear (with approval
prompts). Try: *"search the archive for 'João'"*, *"what's the status of
collections/COLX?"*, *"scan now"*.

For a clickable tree view, page drill-down and one-click pipeline jobs, also
install the PHA extension (`vscode-extension/` in this repo, Phase 1 of
`VSCODE_EXTENSION_SPEC.md`).
