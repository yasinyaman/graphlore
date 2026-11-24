"""Files that change together, read from the project's git history.

Two files that keep changing in the same commits are coupled whether or not
the code says so: a schema and the query that reads it, a handler and the
client that calls it, two modules sharing a convention. This is code-maat's
measure, at file level: per file the commits that touch it (``revs``), per pair
the commits that touch both (``shared``) and their ``degree`` —
``shared / ((revs a + revs b) / 2)``. A pair is coupled from 3 shared commits
and a degree of 0.3.

The history is read with one local ``git log`` (no network), the last
1000 commits that are not merges, from the project directory down
(``--relative``); renames are followed to the file's current name, and a file
git no longer tracks is left out. A commit that touches more than 30 files (a
reformat, a mass rename, a vendored update) says nothing about which of them
belong together and is skipped. A shallow clone, or fewer than 10 commits, is
reported as such: no coupling is then no finding.

The result is cached per project directory and ``HEAD``, so a new commit is
read on the next call and nothing else is.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from . import config

MAX_COMMITS = 1000
BULK_FILES = 30
MIN_SHARED = 3
MIN_DEGREE = 0.3
MIN_COMMITS = 10

_HISTORY_CACHE: dict[tuple[str, str], History] = {}
_HISTORY_CACHE_MAX = 8


@dataclass
class History:
    """What the history says about files changing together."""

    commits: int = 0
    shallow: bool = False
    skipped_bulk: int = 0
    head: str = ""
    # why there is no usable history ("not a git repository", …); "" when read
    reason: str = ""
    revs: dict[str, int] = field(default_factory=dict)
    shared: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return not self.reason and self.commits >= MIN_COMMITS

    def degree(self, a: str, b: str) -> float:
        n = self.shared.get((a, b) if a < b else (b, a), 0)
        mean = (self.revs.get(a, 0) + self.revs.get(b, 0)) / 2
        return n / mean if mean else 0.0

    def coupled(self, a: str, b: str) -> tuple[int, float] | None:
        """(shared commits, degree) when the two files change together."""
        if a == b:
            return None
        n = self.shared.get((a, b) if a < b else (b, a), 0)
        if n < MIN_SHARED:
            return None
        d = self.degree(a, b)
        return (n, d) if d >= MIN_DEGREE else None

    def coupled_pairs(self) -> list[tuple[str, str, int, float]]:
        """Every coupled pair, strongest first."""
        out = []
        for (a, b), n in self.shared.items():
            if n < MIN_SHARED:
                continue
            d = self.degree(a, b)
            if d >= MIN_DEGREE:
                out.append((a, b, n, d))
        out.sort(key=lambda r: (-r[3], -r[2], r[0], r[1]))
        return out

    def partners(self, f: str) -> list[tuple[str, int, float]]:
        """The files `f` changes together with, strongest first."""
        out = []
        for (a, b), n in self.shared.items():
            if f not in (a, b) or n < MIN_SHARED:
                continue
            other = b if a == f else a
            d = self.degree(a, b)
            if d >= MIN_DEGREE:
                out.append((other, n, d))
        out.sort(key=lambda r: (-r[2], -r[1], r[0]))
        return out

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "commits": self.commits, "shallow": self.shallow,
            "skipped_bulk": self.skipped_bulk,
        }
        note = self.note()
        if note:
            out["note"] = note
        return out

    def note(self) -> str:
        """Why the history cannot show what changes together; "" when it can."""
        if self.reason:
            return f"no history read ({self.reason}): files changing together are not known"
        if self.shallow and self.commits < MIN_COMMITS:
            return (f"a shallow clone with {self.commits} commit(s): files changing "
                    "together are not known (fetch more history: git fetch --deepen=1000)")
        if self.commits < MIN_COMMITS:
            return (f"only {self.commits} commit(s) of history: too few to tell which "
                    "files change together")
        return ""


def _git(args: list[str], cwd: Path, timeout: int = 30) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


_Counts = tuple[int, int, dict[str, int], dict[tuple[str, str], int]]


def _parse(log: str, tracked: set[str]) -> _Counts:
    """(commits, bulk commits skipped, revs, shared) from `git log --name-status`
    output, newest commit first."""
    current: dict[str, str] = {}   # a name a file had once -> its name now
    commits = bulk = 0
    revs: dict[str, int] = {}
    shared: dict[tuple[str, str], int] = {}
    for record in log.split("\x1e"):
        lines = [ln for ln in record.splitlines() if ln.strip()]
        if not lines:
            continue
        files: set[str] = set()
        for ln in lines[1:]:
            parts = ln.split("\t")
            status = parts[0][:1]
            if status in ("R", "C") and len(parts) >= 3:
                old, new = parts[1], parts[2]
                now = current.get(new, new)
                files.add(now)
                if status == "R":
                    # older commits name the file by its old path
                    current[old] = now
            elif len(parts) >= 2:
                files.add(current.get(parts[1], parts[1]))
        files &= tracked
        commits += 1
        if len(files) > BULK_FILES:
            bulk += 1
            continue
        for f in files:
            revs[f] = revs.get(f, 0) + 1
        for a, b in combinations(sorted(files), 2):
            shared[(a, b)] = shared.get((a, b), 0) + 1
    return commits, bulk, revs, shared


def history(project_dir: Path | None = None) -> History:
    """The co-change history of the project directory (cached on its HEAD).

    In a workspace (``config.workspace_repos``) each repository's history is read
    on its own and the two are put together, paths led by the repository: files
    of two repositories never share a commit."""
    root = Path(project_dir or config.PROJECT_DIR)
    head = (_git(["rev-parse", "HEAD"], root, timeout=10) or "").strip()
    if not head:
        repos = config.workspace_repos(root)
        if repos:
            return _workspace_history(root, repos)
        return History(reason="not a git repository, or no commit yet")
    return _repo_history(root, head)


def _workspace_history(root: Path, repos: list[str]) -> History:
    parts = [(r, history(root / r)) for r in repos]
    read = [(r, h) for r, h in parts if not h.reason]
    if not read:
        return History(reason="no repository of the workspace has a readable history")
    out = History(
        commits=sum(h.commits for _, h in read),
        shallow=any(h.shallow for _, h in read),
        skipped_bulk=sum(h.skipped_bulk for _, h in read),
        head=",".join(f"{r}@{h.head[:10]}" for r, h in read),
    )
    for r, h in read:
        out.revs.update({f"{r}/{f}": n for f, n in h.revs.items()})
        out.shared.update({(f"{r}/{a}", f"{r}/{b}"): n for (a, b), n in h.shared.items()})
    return out


def _repo_history(root: Path, head: str) -> History:
    key = (str(root.resolve()), head)
    cached = _HISTORY_CACHE.get(key)
    if cached is not None:
        return cached
    shallow = (_git(["rev-parse", "--is-shallow-repository"], root, timeout=10)
               or "").strip() == "true"
    log = _git(["log", "--no-merges", f"-n{MAX_COMMITS}", "--name-status", "-M",
                "--format=%x1e%H", "--relative", "--", "."], root)
    files = _git(["ls-files", "-z"], root)
    if log is None or files is None:
        out = History(head=head, shallow=shallow, reason="git log failed")
    else:
        tracked = {f for f in files.split("\0") if f}
        commits, bulk, revs, shared = _parse(log, tracked)
        out = History(commits=commits, shallow=shallow, skipped_bulk=bulk, head=head,
                      revs=revs, shared=shared)
    if len(_HISTORY_CACHE) >= _HISTORY_CACHE_MAX:
        _HISTORY_CACHE.pop(next(iter(_HISTORY_CACHE)), None)
    _HISTORY_CACHE[key] = out
    return out
