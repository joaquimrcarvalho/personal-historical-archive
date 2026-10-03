# Bug report — an orphaned model-server lock is never reclaimed while its pid looks alive, so one killed job blocks every later job for days

**Status:** **PARTIALLY FIXED** — reported 2026-10-03; **F2 landed the same
day in `d52277e`** (`locks._pid_cmdline` + `locks._looks_like_holder` + 22
tests). A live-looking pid is now reclaimed when its command line proves to be
a different program, which is the pid-reuse shape of §2.1. **Still open:**
F1 (age rule), F3 (heartbeat), F4 (`pha unlock` / orphan reporting) and F5
(honest holder message). The two orphan files were removed by hand on the owner
machine to unblock work (§2.1).
**Severity:** **silent, total and apparently indefinite.** One `pha scan` killed
without running its cleanup leaves lock files that no later job would reclaim
without F2 (which now reclaims a provably reused pid, but still keeps a lock
whose command line cannot be read). Every `pha scan` / `pha edit` / `pha test`
then refuses with "another job is using a model", **`pha search` silently
degrades to keyword-only**, and nothing anywhere reports the holder as dead
— `pha doctor` has no lock/job view at all, `pha status` ignores locks, and
there is no command to clear one. On the owner machine this cost **3 days**
(2026-09-30 06:33 → 2026-10-03 13:14) of an ingestion run on a 7-volume
collection. The documented safeguard against exactly this ("a stale lock must
never wedge every future job") was written in the docstring but **not in the
code**; F2 is the first half of closing that gap, F1 the other (§4).

## 1. Summary

A job holds one slot file per model-server key it may touch, in a user-global
directory. The file records the holder as `<pid> <label>`. Reclaiming a slot is
decided by `locks._stale()`, whose docstring gives **three** rules — dead pid,
pid-less, and *too old*:

> - A recorded pid that is no longer alive -> stale (its holder died).
> - A pid-less file (created but not yet written) -> stale only after the 6 h
>   age threshold […]
> - **A lock OLDER than 6 h is always stale even when its pid looks alive (pids
>   get reused; a stale lock must never wedge every future job).**

The third rule is never executed. `_stale` computes `age` and then, whenever a
pid was recorded, returns a verdict based **only** on process liveness:

```python
def _stale(path: Path, pid: int) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = 0.0
    if pid > 0:
        return pid != os.getpid() and not _pid_alive(pid)   # `age` unused
    return age > STALE_AFTER_S
```

So the only way out is for the recorded pid to become **free**. While that
number belongs to *any* live process — the OS reusing it, or an unreaped
zombie — the lock is immortal. Because the machine rebooted between the death
of the holder and the next job (§2.1), pid reuse is the most likely way the
number stayed occupied; the point of the missing rule is that it does not
matter *why* the pid looks alive.

## 2. Evidence

Measured on the owner machine (macOS, `pha` 0.35.0) and against
`src/personal_historical_archive/locks.py` at `main`.

### 2.1 The incident

| when | what |
|---|---|
| 2026-09-29 18:57 | `pha scan --path collections/litterae-quadrimestres` (pid 28703) takes the locks |
| 2026-09-30 06:33 | the scan stops; its last page write. **The disk had reached 100%** — the likely cause. No release runs. |
| 2026-10-01 14:43 | **the machine reboots** (`kern.boottime` = `Thu Oct 1 14:43:32 2026`) |
| 2026-10-03 12:47 | a corrective `pha edit` over 941 pages refuses, every command: `another job is using a model with no declared server …` |
| 2026-10-03 13:14 | the two lock files are removed by hand (with the owner's approval); work resumes |

### 2.2 The lock files

```
~/Library/Caches/pha/locks/d5b621db29fc.1.lock    14 bytes   mtime Sep 29 18:57
~/Library/Caches/pha/locks/df58248c414f.1.lock    14 bytes   mtime Sep 29 18:57
```

Both contained exactly `28703 pha scan`. `os.kill(28703, 0)` raised
`ProcessLookupError` when checked at 13:10 — the pid no longer existed. Note the
timeline: the holder died on **Sep 30**, the machine **rebooted on Oct 1**, and
the refusal happened on **Oct 3**; over that window a pid of that magnitude was
very plausibly reassigned.

### 2.3 The rule as implemented — demonstration

Run against the module as it stands (a temp `PHA_LOCK_DIR`; `pid` is the test
process's own, so it is certainly alive):

```python
live = os.getpid()
for label, content, age_h in [
    ("pid alive, 1 minute old",  f"{live} pha scan", 1/60),
    ("pid alive, 10 hours old",  f"{live} pha scan", 10),
    ("pid alive, 100 hours old", f"{live} pha scan", 100),
    ("pid dead, 1 minute old",   "999999 pha scan",  1/60),
    ("no pid, 10 hours old",     "",                 10),
]:
    p = write_lock(content, age_h)
    pid, _ = locks._holder(p)
    print(label, "->", locks._stale(p, pid))
```

Result:

| case | `_stale` | reclaimed? |
|---|---|---|
| pid alive, 1 minute | `False` | no |
| pid alive, **10 hours** | `False` | **no** |
| pid alive, **100 hours** | `False` | **no** |
| pid dead | `True` | yes |
| no pid, 10 hours | `True` | yes |

The 100-hour row is the bug in one line: the documented threshold is 6 hours.

### 2.4 Why the file survives the holder

The lock is an ordinary file, and releasing it is an ordinary call —
`locks.release(lock)` sits in a `finally` at each call site (e.g.
`ingest.py:2807`), so an exception, a Ctrl-C or a normal exit cleans up.
**SIGKILL, a hard crash, a full disk that kills the writer, or the machine going
down do not.** There is no `atexit` hook and no signal handler for the lock
files (the only `finally` in `locks.py` closes a file descriptor). A holder that
dies the hard way therefore always leaves its slot behind — by design an
acceptable risk, because `_stale` was meant to clean it up.

### 2.5 What the orphan silently breaks beyond blocking work

`locks.job_running()` is deliberately lock-free and is consulted by read-only
commands so they can avoid *loading* a model and evicting a running job's. With
an orphan in place, `search.py:136` concludes a scan is running and **skips
semantic search**, printing a note that names the dead holder as if it were
alive:

```
A model job (pha scan) is using the embedding server; semantic search skipped …
Keyword results only — re-run when it finishes, pass --force, …
```

So an orphan does not merely stop new work: it also degrades the archive's
search to keyword-only, and tells the user to wait for a job that no longer
exists. `ingest.py:1030` and `ingest.py:2204` consult the same predicate.

### 2.6 Nothing reports it

**Correction (2026-10-03).** An earlier version of this section misread
`pha doctor`'s capacity line as a live-job count. It is not: `pha doctor` has
never observed locks or running jobs.

- `pha status` does not mention locks at all.
- `pha doctor` has **no lock/job view**: the `*  (unlabelled …) 1 job` line is
  the *declared capacity* from `config.yaml`'s `servers:` block
  (`doctor.py`: `cap = f"{slots} jobs" ...`), and it prints identically with
  zero running jobs. It cannot report an orphan today, not because it
  mislabels one but because it says nothing about holders at all (F4).
- There is no `pha unlock`, no `pha doctor --clear-locks`, and no documented
  path to the lock directory. The only remedy is knowing that
  `~/Library/Caches/pha/locks/*.lock` exists.

## 3. Root cause

Two independent defects compose:

1. **The reclamation rule is documented but not implemented.** `_stale()`
   ignores the lock's age whenever a pid is recorded. The intent — "pids get
   reused; a stale lock must never wedge every future job" — is written three
   lines above the code that fails to do it.
2. **A hard-killed holder always leaves its slot.** Nothing else exists to reap
   it: no heartbeat, no liveness cross-check against the *identity* of the
   process, no operator command, no status report.

Defect 1 alone would be survivable if a pid were never reused and the holder
never became a zombie; defect 2 alone would be survivable if `_stale` worked as
documented. Together they produce an indefinite, invisible wedge.

A third, smaller gap made the failure **invisible rather than obvious**: there
is no lock observation surface at all. `pha status` ignores locks and
`pha doctor` only lists declared `servers:` capacity, so nothing distinguishes
"held by a live `pha`" from "held by whatever now answers to that pid".
(Correction 2026-10-03: doctor has no job count to get wrong; see §2.6.)

## 4. Proposed fixes

Ordered as I would land them. F1+F2 together remove the wedge; F4 removes the
need to know the cache path; F3 is a design change and should be decided on its
own.

### F1 — honour the documented age rule (one branch)

```python
def _stale(path: Path, pid: int) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = 0.0
    if pid > 0 and pid != os.getpid():
        # A recorded pid that looks alive is still stolen past the age
        # threshold: pids get reused, and a stale lock must never wedge
        # every future job.
        return age > STALE_AFTER_S or not _pid_alive(pid)
    if pid == os.getpid():
        return False
    return age > STALE_AFTER_S
```

Note the `pid == os.getpid()` case must keep returning `False`
(idempotent re-acquire).

**Trade-off:** a job that legitimately holds a server for more than 6 hours —
our own `pha scan` of the Litterae ran most of a night and the fontes-narrativi
volumes are larger — would have its lock stolen mid-run, admitting a second job
onto a server that holds one model, which is the collision the whole subsystem
exists to prevent. **This is why F1 alone is not enough**, and why the threshold
matters: 6 hours is *below* the runtime of real jobs in this archive. Either the
threshold must rise above the longest expected job, or F3 must make a working
job distinguishable from a wedged one. (Measured here: a `latin-to-english-ocr`
scan of one ~800-page volume takes 2-4 h.)

### F2 — verify the holder's *identity*, not just its liveness

`_pid_alive` answers "is some process using this number". The lock knows more:
it recorded the job label. Confirm the process is actually the job it claims:

- read the command line of `pid` (macOS/Linux: `ps -o command= -p <pid>`, or
  `libproc`/`/proc/<pid>/cmdline`) and require it to look like a `pha` process
  running the recorded label's command;
- if it does not match, the pid was reused → stale, regardless of age.

**Trade-off:** `ps` is an external call on a hot path and its output format
varies (Windows needs `OpenProcess` + `QueryFullProcessImageName`). It is cheap
enough at the few moments `_stale` runs, and it is the *sound* test — it
addresses the actual hazard (a pid that belongs to something else) rather than
approximating it with time. A recorded start time would be better still:
compare `pid` **and** its start time, which no reuse can forge.

**Landed 2026-10-03 (`d52277e`).** `locks._pid_cmdline()` reads
`/proc/<pid>/cmdline` on Linux, `ps -o command= -p` on other POSIX systems, and
the image path only on Windows; `locks._looks_like_holder()` is three-valued
and `_stale()` reclaims only on a proven mismatch. Two deviations from the
sketch above, both to avoid stealing a live lock:

- **An unreadable command line keeps the lock** (`None`). The wedge can still
  outlive a pid whose command cannot be read (sandboxed `ps`, permission
  denied, Windows image-only); stealing a live job is the worse failure.
- **`pha mcp` / `fastmcp` / `*mcp_server*` is a valid holder of any action.**
  `pha_scan_now` runs `scan_once` in the MCP server's own process, so its
  command line names `mcp`, not `scan`; a literal label match would have
  declared a live MCP scan a reused pid and stolen its lock.

### F3 — heartbeat (the only fix that distinguishes *working* from *wedged*)

The holder touches its slot files periodically (e.g. every 60 s) from the same
thread that already reports progress. A lock untouched for N intervals is
wedged, whatever its pid says, and a lock touched a second ago is live, however
old.

**Open design questions, to decide before implementing:**
- **The slot file's mtime is currently the acquisition time** and is read by
  `holder_age_s()` to print "held for 1h12m" in `--wait`. A heartbeat destroys
  that meaning, so the acquisition time must move somewhere durable — a second
  line in the file, a sidecar stamp, or atomically rewritten content
  (`<pid> <acquired-epoch> <label>`). Changing the file's content is a
  compatibility question for a file shared between two `pha` versions on one
  machine; decide it deliberately.
- What N is: the longest gap between touches in a healthy job is one batch, so
  N must exceed the slowest batch (a long model call). Too tight and a working
  job is stolen.
- Whether the heartbeat thread exists at all in short jobs, and what happens
  when a heartbeat write fails — **including on a full disk**, which is the very
  condition that produced this incident.

Until F3 lands, F1's threshold must be set above the longest real job.

### F4 — make an orphan visible and clearable

- `pha status` (and `pha doctor`) should report a held lock whose holder is
  provably gone or provably not a `pha` process as **orphaned**, with its age
  and the pid, and not count it as a running job.
- `pha unlock [--force] [--all]` — clear stale slots, refusing anything that
  looks live unless forced. (Or `pha doctor --clear-locks`; a top-level verb is
  easier to discover.)
- Name the lock directory in the orphan message, so the manual escape hatch is
  discoverable even without the command.

### F5 — small: stop misreporting the cause

`search.py`'s note and `LockHandle.reason()` should say the holder *looks*
alive and give its age, so an orphan reads as a suspicion rather than a fact:

```
A model job (pha scan, pid 28703, holding for 4d 2h) …
```

Cheap, and it is what would have made this incident obvious on day one.

## 5. Test plan

The behaviour is already covered for the pid-less branch
(`tests/test_locks.py:134`, `test_pidless_fresh_lock_is_respected_but_old_is_stale`)
— which is precisely why the missing branch was never noticed. Add:

1. **`test_old_lock_with_live_pid_is_stale`** — a foreign lock with a live pid
   (monkeypatched `_pid_alive` → `True`) and an mtime past the threshold must be
   reclaimed. This test **fails on `main` today**.
2. **`test_young_lock_with_live_pid_is_respected`** — the same, one minute old,
   must still refuse.
3. **`test_reused_pid_is_not_a_holder`** (F2) — **DONE** (`d52277e`). The
   suite also covers a matching holder, a different `pha` action, the
   in-process MCP host, and an unreadable command line (see References).
4. **`test_wedged_holder_is_stolen_after_heartbeat_timeout`** (F3) — a holder
   that stops touching is stolen; one that keeps touching is not, even when old.
5. **`test_doctor_reports_orphan`** / **`test_status_reports_orphan`** (F4) —
   an orphan is listed as orphaned and not counted as a job.
6. **`test_unlock_clears_only_orphans`** (F4) — refuses a live holder.
7. **Regression for the wedge:** with an orphan present, `pha search` must not
   claim a model job is running — **DONE for F2**
   (`test_job_running_ignores_a_reused_pid`): the slot is found free and search
   embeds normally. F1's age-based case and F5's message are still open.

## 6. Impact and cost

- **Blast radius is machine-wide, not archive-wide.** `lock_dir()` is
  deliberately user-global so two archives sharing a model server serialise; an
  orphan therefore stops work in *every* archive on the machine, and (via
  `job_running`) degrades semantic search in all of them.
- **It costs days, not minutes**, because the failure is indistinguishable from
  a long-running job and nothing surfaces it. Measured here: 3 days.
- **It compounds with the disk problem that caused it.** The holder in this
  incident died when the disk hit 100% — the same condition that makes a
  heartbeat write fail, which F3 must therefore handle rather than assume.
- No data loss: nothing is corrupted, and the blocked work resumes once the
  slots are free.

## 7. Method note

- The lock layout, the key model and the intent of `_stale` were read from
  `src/personal_historical_archive/locks.py` on `main`, with the design
  rationale in `pha-per-server-model-lock-enhancement-request.md`.
- The demonstration in §2.3 was run against the module as it stands, in a
  throwaway `PHA_LOCK_DIR`, so no real lock was touched.
- **Not proven, and flagged as inference:** *why* the recorded pid looked alive
  on 2026-10-03. `os.kill(28703, 0)` failed by 13:10, but the machine had
  rebooted on Oct 1 and a reused pid is the most likely explanation; an
  unreaped zombie would produce the same observation. The fix does not depend on
  which it was — both are "the pid answers, the job is not working", which is
  what the missing rule covers.
- The two orphan files were removed by hand (with the owner's explicit
  approval) to restore service; the directory was empty at report time. F2 was
  implemented later the same day in `d52277e` (see §4).

## 8. Relation to existing docs, and scope

- **Extends** `pha-per-server-model-lock-enhancement-request.md` (the design
  that introduced per-server keyed locking and the `STALE_AFTER_S` constant).
  That document's §8 "Open questions" is the natural home for the F3 decisions;
  its `_stale` behaviour is asserted there but not specified to this depth.
- **The same class as** the other silent-success-looking defects recorded here:
  `pha-reindex-skips-non-done-documents-bug-report.md` stores a wrong result as
  `done`; this one stores a dead job as `running`.
- **Still to do (2026-10-03):** add an entry to
  `enhancements/pha-enhancement-requests-INDEX.md` under **open**, next to the
  lock/embedding rows, marking F2 landed / F1+F3+F4+F5 open.
- Affects every pha user, not this archive: nothing here is archive-specific.

## References

- `src/personal_historical_archive/locks.py` — `STALE_AFTER_S` (:69),
  `_pid_alive` (:200), `_holder` (:227), **`_stale` (:406)**, `_pid_cmdline`
  (:337), `_looks_like_holder` (:304), `_acquire_key` (:479), `_first_foreign`
  (:521), `_take_all` (:592), `holder_age_s` (:623), `release` (:644),
  `job_running` (:659)
- `src/personal_historical_archive/search.py:136` — the silent keyword-only
  fallback
- `src/personal_historical_archive/ingest.py:1030`, `:2204` — other lock-free
  observers; `:2807` — a `finally` release
- `tests/test_locks.py:134` — the pid-less staleness test (the branch that
  works); the F2 tests live in the `holder identity (F2)` section of the same
  file (`test_looks_like_holder`, `test_reused_pid_is_not_a_holder`, ...)
- `enhancements/pha-per-server-model-lock-enhancement-request.md`
