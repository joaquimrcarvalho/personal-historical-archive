#!/usr/bin/env python3
"""Report where LM Studio models live, for pha/model-server decisions.

LM Studio's own API (`/api/v0/models`, `/v1/models`) reports model ids and
`state: loaded|not-loaded`, but not *where* a model is loaded. With LM Link a
request to `localhost:1234` can be served by another device, and a model loaded
remotely can win over a local copy that is not loaded.

The `lms` CLI does report locality:

* ``lms ls --json``      model copies on disk; ``deviceIdentifier`` null = local
* ``lms ps --json``      loaded instances; ``deviceIdentifier`` null = local
* ``lms link status --json``  device id -> device name, this device included
* ``lms server status --json``  local server running/port

This script joins those views. It never loads or unloads a model; when a local
copy is present but not loaded it prints the exact ``lms load`` command to make
one, with a unique identifier that pha can then address directly.

Usage::

    python3 lmstudio_locality.py                 # every model
    python3 lmstudio_locality.py --model qwen    # filter by id substring
    python3 lmstudio_locality.py --json
    python3 lmstudio_locality.py --model qwen --require-local

Exit status is 0 normally, and with --require-local it is 0 only when every
matched model has a locally loaded instance.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


def run_json(argv: list[str]) -> object:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit("lms not found on PATH (LM Studio CLI is not installed)")
    text = (proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SystemExit("failed: " + " ".join(argv) + (": " + detail if detail else ""))
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # `lms` sometimes prints progress/log lines before the JSON body.
        for opener in ("[", "{"):
            start = text.find(opener)
            if start >= 0:
                try:
                    return json.loads(text[start:])
                except json.JSONDecodeError:
                    continue
        raise SystemExit("could not parse JSON from: " + " ".join(argv))


def link_status() -> dict:
    try:
        data = run_json(["lms", "link", "status", "--json"])
    except SystemExit:
        return {}
    return data if isinstance(data, dict) else {}


def server_status() -> dict:
    try:
        data = run_json(["lms", "server", "status", "--json"])
    except SystemExit:
        return {}
    return data if isinstance(data, dict) else {}


def device_names(link: dict) -> dict[str, str]:
    names: dict[str, str] = {}
    for peer in link.get("peers") or []:
        dev = peer.get("deviceIdentifier")
        if dev:
            names[str(dev)] = str(peer.get("deviceName") or dev)
    dev = link.get("deviceIdentifier")
    if dev:
        names[str(dev)] = str(link.get("deviceName") or dev)
    return names


def is_current(dev, current: str | None) -> bool:
    if dev in (None, ""):
        return True
    if current and str(dev) == str(current):
        return True
    return False


def is_local_disk(entry: dict, current: str | None) -> bool:
    dev = entry.get("deviceIdentifier")
    path = str(entry.get("path") or "")
    if not is_current(dev, current):
        return False
    # Defensive: a remote path is prefixed with the remote device id.
    match = re.match(r"^([0-9a-f]{32}):", path)
    if match and (not current or match.group(1) != str(current)):
        return False
    return True


def suggested_identifier(model_key: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", "pha-local-" + model_key).strip("-")
    return cleaned[:80] or "pha-local-model"


def device_label(dev, names: dict[str, str], current: str | None) -> str:
    if dev in (None, ""):
        return "this device"
    if current and str(dev) == str(current):
        return str(names.get(str(dev), "this device"))
    return str(names.get(str(dev), "remote:" + str(dev)[:12]))


def collect(ls: list[dict], ps: list[dict], current: str | None) -> dict:
    models: dict[str, dict] = {}

    def entry_for(key: str, kind: str) -> dict:
        model = models.setdefault(key, {
            "modelKey": key,
            "type": kind,
            "disk": {"local": False, "local_path": None, "remote": []},
            "loaded": {"local": [], "remote": []},
        })
        if kind and not model.get("type"):
            model["type"] = kind
        return model

    for item in ls:
        if not isinstance(item, dict):
            continue
        key = str(item.get("modelKey") or item.get("indexedModelIdentifier") or item.get("path") or "?")
        model = entry_for(key, str(item.get("type") or ""))
        if is_local_disk(item, current):
            model["disk"]["local"] = True
            model["disk"]["local_path"] = item.get("path")
        else:
            dev = item.get("deviceIdentifier")
            if dev and dev not in model["disk"]["remote"]:
                model["disk"]["remote"].append(dev)

    for item in ps:
        if not isinstance(item, dict):
            continue
        key = str(item.get("modelKey") or item.get("identifier") or "?")
        model = entry_for(key, str(item.get("type") or ""))
        record = {
            "identifier": item.get("identifier"),
            "status": item.get("status"),
            "deviceIdentifier": item.get("deviceIdentifier"),
            "contextLength": item.get("contextLength"),
            "ttlMs": item.get("ttlMs"),
        }
        if is_current(item.get("deviceIdentifier"), current):
            model["loaded"]["local"].append(record)
        else:
            model["loaded"]["remote"].append(record)

    for model in models.values():
        if model["loaded"]["local"]:
            model["verdict"] = "local_loaded"
        elif model["disk"]["local"]:
            model["verdict"] = "local_available_not_loaded"
        elif model["loaded"]["remote"]:
            model["verdict"] = "remote_loaded_only"
        elif model["disk"]["remote"]:
            model["verdict"] = "remote_only"
        else:
            model["verdict"] = "not_found"
    return models


def recommendation(model: dict) -> str:
    key = model["modelKey"]
    if model["verdict"] == "local_loaded":
        ids = [str(r.get("identifier")) for r in model["loaded"]["local"] if r.get("identifier")]
        return "local instance loaded; address it by identifier: " + ", ".join(ids)
    if model["verdict"] == "local_available_not_loaded":
        ident = suggested_identifier(key)
        return (f"pre-load the local copy: lms load {key} -y --identifier {ident}; "
                f"then set `model: {ident}` in the pha model file")
    if model["verdict"] == "remote_loaded_only":
        return ("only a remote instance is loaded; pre-load the local copy first if "
                "the pass must run locally, or accept the remote device and record it "
                "in the model file's `server:` label")
    if model["verdict"] == "remote_only":
        return ("no local disk copy; download it locally or accept the remote copy, "
                "recording the remote device in the model file's `server:` label")
    return "not on local disk and not visible through LM Link"


def filter_models(models: dict, needle: str | None) -> list[dict]:
    rows = [m for m in models.values()]
    if needle:
        low = needle.lower()
        rows = [m for m in rows
                if low in str(m.get("modelKey", "")).lower()
                or any(low in str(r.get("identifier", "")).lower() for r in m["loaded"]["local"])
                or any(low in str(r.get("identifier", "")).lower() for r in m["loaded"]["remote"])]
    return sorted(rows, key=lambda m: str(m.get("modelKey")))


def render_human(payload: dict, rows: list[dict]) -> None:
    link = payload.get("link") or {}
    server = payload.get("server") or {}
    print("LM Studio locality")
    print(f"  this device : {link.get('deviceName') or '(LM Link disabled or unavailable)'}"
          f" {link.get('deviceIdentifier') or ''}".rstrip())
    print(f"  LM Link     : {link.get('status') or 'unknown'}"
          f" ({len(link.get('peers') or [])} peer(s))")
    if server:
        print(f"  local server: {'running' if server.get('running') else 'not running'}"
              f" on port {server.get('port')}")
    if not rows:
        print("  models      : none matched")
        return
    for model in rows:
        print()
        print(f"  {model['modelKey']}  [{model.get('type') or 'model'}]")
        print(f"    verdict: {model['verdict']}")
        disk = model["disk"]
        if disk["local"]:
            print(f"    local disk copy: {disk['local_path']}")
        names = payload.get("device_names") or {}
        if disk["remote"]:
            remotes = ", ".join(device_label(d, names, payload.get("deviceIdentifier")) for d in disk["remote"])
            print(f"    remote disk copies: {remotes}")
        for rec in model["loaded"]["local"]:
            print(f"    loaded local: {rec.get('identifier')} ({rec.get('status')})")
        for rec in model["loaded"]["remote"]:
            name = device_label(rec.get("deviceIdentifier"), names, payload.get("deviceIdentifier"))
            print(f"    loaded remote: {rec.get('identifier')} on {name} ({rec.get('status')})")
        print(f"    -> {recommendation(model)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="report LM Studio model locality")
    parser.add_argument("--model", default=None,
                        help="only models whose id contains this substring")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--require-local", action="store_true",
                        help="exit non-zero unless every matched model is loaded locally")
    args = parser.parse_args()

    link = link_status()
    server = server_status()
    current = link.get("deviceIdentifier")
    try:
        ls = run_json(["lms", "ls", "--json"]) or []
        ps = run_json(["lms", "ps", "--json"]) or []
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not isinstance(ls, list):
        ls = []
    if not isinstance(ps, list):
        ps = []

    models = collect(ls, ps, str(current) if current else None)
    rows = filter_models(models, args.model)
    names = device_names(link)
    if args.json:
        print(json.dumps({
            "deviceIdentifier": current,
            "deviceName": link.get("deviceName"),
            "link": link,
            "server": server,
            "device_names": names,
            "models": rows,
        }, ensure_ascii=False, indent=2))
    else:
        render_human({
            "link": link,
            "server": server,
            "deviceIdentifier": current,
            "device_names": names,
        }, rows)

    if args.require_local:
        missing = [m for m in rows if m.get("verdict") != "local_loaded"]
        if missing:
            print("not loaded locally: " + ", ".join(str(m.get("modelKey")) for m in missing),
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
