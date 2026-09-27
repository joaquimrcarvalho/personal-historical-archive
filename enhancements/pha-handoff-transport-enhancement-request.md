# Enhancement request — move a hand-over payload without ssh keys (`--send` / `pha handoff recv`)

**Status: IMPLEMENTED** (2026-09-26) — `handoff_transport.py` + `--send` /
`pha handoff recv` / `pha handoff peers`, with `tests/test_handoff_transport.py`,
**and the unattended trigger** `handoff_worker.py` + `pha handoff worker`
(`tests/test_handoff_worker.py`) — see §9, which is what the first cut missed.
The three open questions below were answered at build time: (1) `--send` on the
existing verbs, no new `send` verb; (2) `recv` **stages and prints the command**
— it never merges on its own; (3) the drop box (`recv --loop`) ships but is
optional, and nothing installs a daemon.
**Written:** 2026-09-26 against pha 0.35.0.
**One line.** Keep the payload format exactly as it is; give `pha handoff` an
optional *transport* so the tool itself moves the directory across the tailnet —
no ssh, no shared keys, no open port, no mount.

---

## 1. Problem

`handoff.py` states the design plainly (`handoff.py:13-15`):

> The payload is a directory; moving it (rsync, a share, a USB stick) is the
> user's business.

That was the right cut for the merge logic and the lease. It is the wrong cut
for the person. In practice the "user's business" is **ssh + shared keys**, and
each hand-over costs three technical steps the archive owner should not have to
own: creating/distributing a key, knowing the peer's address, and knowing the
remote path — twice (out, then back). The owner of this archive is a historian,
and the tool's own rule is that pha is driven through its interface, not by
hand-rolled plumbing around it.

Nothing about the *merge* needs to change. This request is only about the bytes
between `out` and `in`, and between `back` and `fetch`.

## 2. Why "a temporary mount" is the wrong primitive

A mount was the first suggestion, and it is worth recording why it is not the
answer:

- **An SMB/AFP share** (macOS File Sharing) needs the sharing pane enabled —
  a GUI, usually an administrator, and persistent state on the machine. Not
  autonomous, and not "light".
- **Taildrive** (`tailscale drive share`) is *literally* a share mounted at
  `/Volumes/<name>` — the closest thing to the suggestion — but it is alpha,
  needs a tailnet admin flag turned on, and mounts over WebDAV. Heavy for a
  transfer that happens twice per hand-over.
- **`tailscale serve <directory>`** exposes a directory on the tailnet over
  HTTPS with a real certificate, but it is **read-only** (a drop needs the
  other direction), it needs MagicDNS + HTTPS certificates enabled in the admin
  console, and it **persists** until `tailscale serve reset` — the opposite of
  temporary.

A mount is persistent state that needs privileges. A hand-over is a one-shot
transfer between two machines that are, by the design's own premise, **both
awake at that moment**. So the right primitive is a one-shot send, not a mount.

## 3. The primitive that fits: Taildrop

Verified on the archive machine (macOS, tailscale 1.102.4):

| command | what it does |
|---|---|
| `tailscale file cp <files...> <target>:` | send a file to a tailnet device |
| `tailscale file cp --targets` | list the devices that can receive |
| `tailscale file get [--wait] [--loop] [--conflict=rename] <dir>` | move received files out of the inbox |
| `tailscale status --json` | peer `HostName` / `DNSName` / `TailscaleIPs` / `Online` |

`--loop` is the "drop folder": it keeps receiving as files arrive. `--wait`
blocks until one does. `--targets` means pha never has to ask the human for an
address.

**Security is the tailnet, not our code.** Taildrop is peer-to-peer over
WireGuard between devices already authenticated to the same tailnet; it opens no
listening port, involves no key material the user handles, and a device outside
the tailnet cannot reach it. "Light security, only in the same network" is
precisely what it already is — the network *is* the boundary.

## 4. Design

**Transport envelope.** Tar the payload directory into one file,
`<name>.pha-handoff.tgz`. The on-disk payload format (`handoff.json`,
`HANDOFF_VERSION = 1`) is **unchanged** — the archive is unpacked back to a
directory before `import_handoff` / `build_result` ever see it. The tarball is
only how it travels; it also makes the transfer atomic and gives `recv`
something unambiguous to recognise.

**Surface.** Two verbs, matching "drop" and "get":

```
pha handoff out  <targets> --send <peer>    # export as today, then tar + send
pha handoff back <dir>     --send <peer>    # the worker's return trip
pha handoff recv [--wait] [--loop] [--from PATH] [--no-downloads]
pha handoff peers [--json]                  # who pha can send to (name, IP, online)
```

- `--send <peer>` resolves a **name substring** against the Taildrop targets
  (`tailscale file cp --targets`, which prints `IP<TAB>hostname<TAB>status` and
  is therefore both the list and the address). Ambiguous → list the candidates
  and stop. Offline → refuse with the reason (Taildrop is a direct transfer;
  there is no server queue). An IP is accepted directly.
- `pha handoff recv` is the ease the request is for: it does
  `tailscale file get --conflict=rename <staging>`, unpacks, and **auto-detects**
  what arrived — a manifest (`handoff.json`) is a payload, a result
  (`handoff-result.json`) is a return — then **prints the command** to run it
  (`pha handoff in …` / `pha handoff fetch …`). It never merges on its own; the
  human never types a path.
- The tarball is named from the hand-off id, so `recv` never guesses and two
  hand-overs cannot silently collide.
- `--copy` / the existing directory behaviour stays the default and the
  fallback (USB stick, an ssh/rsync user who wants it, a machine without
  Tailscale).

**Staging.** Received files land in `<archive>/.pha/handoffs/incoming/<id>/`,
machine-local and gitignored like the leases beside them.

## 5. Security model, stated plainly

- The trust boundary is **tailnet membership** — device-authenticated by
  Tailscale, not by anything pha invents.
- **No inbound port**, nothing to firewall, no key the historian handles.
- **Honest limitation:** any device in the tailnet can Taildrop to the machine.
  That is the "light" the request asks for. Tighter (~"only this peer") means
  tailnet ACLs, which is the admin console's job, not pha's.
- **A tar arriving over the network is untrusted input even on a tailnet.**
  Unpacking MUST reject members whose resolved path escapes the staging
  directory (absolute paths, `..`, symlink targets). This is a real requirement
  of the feature, not decoration — a malicious or merely buggy peer must not be
  able to write outside the staging dir.

## 6. Caveats to verify before building

All four were handled rather than merely noted:

1. **Both machines must be online at send time.** `resolve_peer` refuses an
   offline peer before anything is packed, and names the reason ("Taildrop is a
   direct transfer, so the machine must be awake to receive. Wake it and try
   again.").
2. **Taildrop must be enabled.** Handled by an explicit error rather than a
   `pha doctor` line: the `engines` report is engine-shaped (a binary that some
   model file *declares*), and Tailscale is never declared by a model file.
   `pha handoff peers` is the probe — it either lists devices or says Tailscale
   is missing, and `require_tailscale` explains the install-or-use-a-directory
   choice.
3. **macOS delivery is ambiguous.** `recv` looks in the CLI inbox first, then in
   `~/Downloads` for archives it has not already unpacked (`--no-downloads`
   turns the second off). An unreadable `~/Downloads` (macOS TCC, a sandboxed
   agent context) is treated as "nothing there" — the first end-to-end run of
   `pha handoff recv` died with `PermissionError` on exactly that path.
4. **Payload size.** The tar is streamed to and from disk (never buffered in
   memory), the size and elapsed time are printed while packing, and `send`
   resolves the peer **before** packing, so a bad peer name cannot cost a
   multi-GB tar.

## 7. Non-goals

- **No daemon required.** `worker --install` is an explicit, separate command
  with its own `--status`/`--uninstall`, and `--dry-run` to see it first —
  nothing installs a LaunchAgent as a side effect of a hand-over, and the plain
  directory commands never depend on one running.
- **No change** to the lease, the manifest, the result format, or the merge
  rules.
- **No new dependency** beyond the `tailscale` CLI that is already present on a
  machine that is on a tailnet.

## 8. Open questions for the owner

1. `--send` on the existing verbs (as specced) or a separate
   `pha handoff send <dir> --to <peer>`?
2. Should `recv` apply automatically, or always print the command and require
   `--apply`? (Applying is the merge — printing first is safer, and matches the
   dry-run discipline elsewhere.)
3. Do you want the optional drop box (`recv --loop`), or strictly one-shot?

---

## 9. As implemented — the trigger, which §1 missed

The first cut replaced **ssh to move the files** and left **ssh to press go**.
That is a real gap, not a detail: Taildrop delivers a payload, it does not run
anything on the receiving machine, so somebody still has to be at the Mac mini
(or ssh into it) to run `in` → `work` → `back`.

`pha handoff worker --send-to <owner>` closes it:

- it watches the Tailscale inbox and, per hand-over, runs
  `handoff in` → `handoff work --resume` → `handoff back` → send;
- **it drives those as subprocesses of the documented CLI**, never by importing
  the pipeline. The archive's own rule — work through the interface — is what
  decides this, and it keeps the worker an orchestrator rather than a second
  implementation that can drift from `pha handoff work`;
- **it waits for the owner**, which is the point: Taildrop refuses an offline
  peer and a closed MacBook is the normal case, so the worker retries on
  `PeerUnavailable` (a new exception type, precisely so this is distinguishable)
  until the lid opens. It does **not** retry a misspelled or ambiguous peer
  forever — that fails loudly and leaves the packed result on disk;
- `--install` writes a **user LaunchAgent** (`~/Library/LaunchAgents`, no
  administrator password, nothing outside the home folder and `.pha/`), running
  the PATH-proof `<python> -m personal_historical_archive` form from the
  location trace so it survives a minimal login PATH; `--dry-run` prints the
  change, `--status`/`--uninstall` manage it, `--once` drains and exits;
- an archive that arrives and cannot be staged (a refused tar) is logged and
  skipped — one bad hand-over must not kill a resident worker.

**What it cannot do**, stated so it is not mistaken for magic: it cannot wake a
sleeping LM Studio, so the models must be running on the working machine; a
stage that finds none fails loudly in `<archive>/.pha/handoff-worker.log`
instead of hanging. And an `--install`ed worker is a persistent process on that
machine — which is a real change worth telling the owner about before making it.
