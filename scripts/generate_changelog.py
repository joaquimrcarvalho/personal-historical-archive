"""Generate a packaged CHANGELOG.json entry from git commits."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "src/personal_historical_archive/CHANGELOG.json"
_KNOWN_TYPES = {"feat", "fix", "perf", "refactor", "docs", "test", "chore"}

def parse_subject(subject: str) -> dict[str, str]:
    text = subject.strip()
    head, sep, tail = text.partition(":")
    if not sep:
        return {"type": "other", "scope": "", "text": text}
    kind_scope = head.strip()
    scope = ""
    kind = kind_scope
    if kind_scope.endswith(")") and "(" in kind_scope:
        kind, _, rest = kind_scope.partition("(")
        scope = rest[:-1].strip()
    kind = kind.strip().lower()
    desc = tail.strip()
    if kind not in _KNOWN_TYPES or not desc:
        return {"type": "other", "scope": "", "text": text}
    return {"type": kind, "scope": scope, "text": desc}


def entry_from_git_log(text: str, *, version: str, summary: str = "",
                       today: str | None = None) -> dict:
    changes: list[dict[str, str]] = []
    for record in text.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        fields = record.split("\x1f")
        if len(fields) < 2:
            continue
        commit, subject = fields[0].strip(), fields[1].strip()
        if not subject or subject.lower().startswith("release:"):
            continue
        change = parse_subject(subject)
        change["commit"] = commit[:12]
        changes.append(change)
    return {
        "date": today or _dt.date.today().isoformat(),
        "summary": summary.strip(),
        "changes": changes,
    }


def _git_log(since: str) -> str:
    proc = subprocess.run(
        ["git", "log", "--no-merges", "--pretty=format:%H%x1f%s%x1f%b%x1e",
         f"{since}..HEAD"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode:
        raise SystemExit(proc.stderr.strip() or "git log failed")
    return proc.stdout


def load_changelog(path: Path) -> dict:
    if not path.exists():
        return {"schema": 1, "releases": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise SystemExit(f"unsupported changelog schema in {path}")
    if not isinstance(data.get("releases"), dict):
        raise SystemExit(f"changelog has no releases object: {path}")
    return data


def write_changelog(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--today", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    entry = entry_from_git_log(
        _git_log(args.since), version=args.version,
        summary=args.summary, today=args.today,
    )
    data = load_changelog(args.output)
    data["releases"][args.version] = entry
    if args.dry_run:
        print(json.dumps({args.version: entry}, ensure_ascii=False, indent=2))
        return
    write_changelog(args.output, data)
    print(f"updated {args.output}: {args.version} "
          f"({len(entry['changes'])} changes)")


if __name__ == "__main__":
    main()
