"""graphlore's built-in code search: definition-aligned chunks, BM25F, and an
optional dense tier.

The answer to "where is the code that does X" without the semble extra. Two tiers:

- **lexical, always on, stdlib only**: an identifier-aware tokenizer
  (``getUserName``, ``get_user_name`` and ``GetUserName`` share a term; their parts
  are terms too) feeding BM25F over three fields — the definition's name and
  qualified name, the file path, and the body — plus a small overlay built from
  the graph (the docstring a node carries, the names of what it calls and what
  calls it).
- **dense, optional** (``[embed]`` extra, :mod:`graphlore.embed`): a static
  embedding of each chunk, fused with the lexical ranking by weighted reciprocal
  rank. When the extra or the model is missing the index answers lexically and
  ``info()`` says why.

What semble does not have: chunks are the span engine's definitions (one per
function, method or class, a container holding only its own lines), so every hit
joins to its graph node exactly; module-level code between definitions becomes
low-weight ``module`` chunks; files the span engine cannot read (another language
without ``[treesitter]``) are cut into fixed windows and counted.

The index is built once per project and refreshed incrementally: each call stats
the project's files and re-chunks only the ones whose (mtime, size) changed.

The hot loop sits behind :class:`_Kernel` (``tokenize`` and ``bm25f``), whose
reference is :class:`_PyKernel`. An optional accelerator module
(``graphlore_accel``, not shipped) that exposes ``KERNEL_ABI == 1`` and a
``Kernel`` class with the same two methods replaces it; every logarithm is
computed here, so a port does only additions, multiplications and divisions and
can return bit-identical scores (``tests/fixtures/search_golden.json``).

Depends on config, graph, spans, scope, review (the walk's skip lists) and embed.
"""
from __future__ import annotations

import heapq
import math
import os
import re
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from . import config, embed
from .graph import (
    _CALL_RELATIONS,
    _edge_ends,
    _is_test_path,
    _node_file,
    _node_id,
    _node_label,
    _node_line,
    _rel,
)
from .review import _CODE_EXTS, _MENTION_SKIP_DIRS
from .scope import _minified_by_content, _vendored_by_path
from .spans import _norm_relpath, _resolve_in_project, _spans_for_file

# Source files the index reads: the review layer's code extensions plus a few
# languages that have no graph nodes of their own but are still code to find.
_SEARCH_EXTS = _CODE_EXTS | frozenset({
    ".vue", ".svelte", ".dart", ".hs", ".ml", ".mli", ".r", ".jl", ".erl", ".hrl",
    ".clj", ".cljs", ".pl", ".pm", ".sh", ".bash", ".sql", ".groovy", ".fs", ".nim",
})
_SEARCH_MAX_FILE_BYTES = 1_000_000
_SEARCH_MAX_FILES = 20_000
_SEARCH_MAX_CHUNKS = 200_000
# Files without spans, and long runs of module-level code, are cut into windows.
_WINDOW_LINES = 40
# Fewer non-blank uncovered lines than this between definitions are not a chunk.
_GAP_MIN_LINES = 3
# Module-level code (imports, constants, a script's body) says the file mentions
# the topic more often than that it answers the question.
_MODULE_WEIGHT = 0.5

# BM25F. Name and path are short fields: barely length-normalised.
_K1 = 1.2
_W_NAME, _W_PATH, _W_BODY = 4.0, 1.5, 1.0
_B_NAME, _B_PATH, _B_BODY = 0.3, 0.3, 0.75
# The graph overlay (docstring and call-neighbour names) is its own BM25 index,
# added at this weight.
_OVERLAY_WEIGHT = 0.6
_OVERLAY_NEIGHBOURS = 12
# A symbol query that is a chunk's whole qualified name, or its last component.
_EXACT_QUALNAME_BOOST = 3.0
_EXACT_NAME_BOOST = 2.0
_QUERY_MAX_TERMS = 64

# Fusion of the lexical and dense rankings: weighted reciprocal rank.
_RRF_K = 60
_FUSION_DEPTH = 50
# (lexical, dense) weight by query kind: a name is found by its spelling, a
# question by its meaning.
_FUSION_WEIGHTS = {"symbol": (1.0, 0.3), "natural": (0.6, 1.0), "code": (0.5, 1.0)}
# Rank fusion forgets how far ahead an exact name match was in BM25: on a symbol
# query a chunk named exactly like the query gains this many first places.
_EXACT_FUSED_BONUS = 0.0

# find_related on the lexical tier: candidates come from the postings of the
# chunk's heaviest terms, then exact cosine on the best of them.
_RELATED_TERMS = 32
_RELATED_CANDIDATES = 200

# Where a chunk lives says how likely it is to be the answer: test code, examples
# and type stubs rank below the implementation unless the query asks for them.
_TEST_WEIGHT = 0.4
_EXAMPLE_WEIGHT = 0.5
_STUB_WEIGHT = 0.7
_EXAMPLE_DIRS = frozenset({"example", "examples", "_examples", "demo", "demos", "docs_src",
                           "samples", "sample", "benchmark", "benchmarks"})
_TEST_WORDS = frozenset({"test", "tests", "testing", "spec", "specs", "fixture", "fixtures",
                         "mock", "mocks"})
# The n-th chunk of one file in a ranking is worth this much less than the one
# before it: a question with two homes should show both.
_FILE_DECAY = 0.6
# A file several of whose chunks match is more likely the answer: its best chunk
# gains this share of the others' scores.
_FILE_COHERENCE = 0.0
# The same on the BM25-only index, for questions (not names: a symbol query wants
# the one definition, and a file that mentions the name often loses it).
_LEX_COHERENCE = 0.0
_RERANK_DEPTH = 100

_INDEX_CACHE_MAX = 2
_KERNEL_ABI = 1

_STOPWORDS = frozenset("""
a an and any are as at be been being but by can could do does doing done each
for from get gets has have how i if in into is it its me my of on or our should
so some than that the their them then there these they this those to up us was
we were what when where which who whom why will with would you your code
function functions method methods class classes implementation implemented
implement handle handles handled handling logic find finds show shows used use
uses
""".split())


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z0-9_$\u0080-\U0010ffff]+")
# Camel humps, acronyms and digit runs; non-ASCII letters count as lower case;
# `_` and `$` split.
_PART = re.compile(r"[A-Z]+(?![a-z\u0080-\U0010ffff])|[A-Z]?[a-z\u0080-\U0010ffff]+|[0-9]+")
_TERM_CACHE: dict[str, tuple[str, ...]] = {}
_TERM_CACHE_MAX = 500_000


def _stem(w: str) -> str:
    """A small, fixed suffix stemmer: plurals, -ing, -ed, a trailing -e. Enough to
    join ``retries`` / ``retry`` / ``retrying`` and ``parse`` / ``parsed``."""
    if len(w) > 4 and w.endswith(("ies", "ied")):
        w = w[:-3] + "y"
    elif len(w) > 4 and w.endswith("sses"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def _word_terms(word: str) -> tuple[str, ...]:
    """Terms of one identifier-like word: its whole folded form when it has
    several parts (``getusername``), and each part of two or more characters,
    stemmed. Pure numbers are not terms."""
    cached = _TERM_CACHE.get(word)
    if cached is not None:
        return cached
    parts = [p.lower() for p in _PART.findall(word)]
    terms: list[str] = []
    if len(parts) == 1:
        p = parts[0]
        if len(p) >= 2 and not p.isdigit():
            terms.append(_stem(p))
    elif parts:
        whole = "".join(parts)
        if not whole.isdigit():
            terms.append(whole)
        for p in parts:
            if len(p) >= 2 and not p.isdigit():
                t = _stem(p)
                if t != whole:
                    terms.append(t)
    out = tuple(terms)
    if len(_TERM_CACHE) >= _TERM_CACHE_MAX:
        _TERM_CACHE.clear()
    _TERM_CACHE[word] = out
    return out


class _Kernel(Protocol):
    """The two hot loops, replaceable by an accelerator with the same results."""

    name: str

    def tokenize(self, text: str) -> list[str]: ...

    def bm25f(
        self,
        terms: list[tuple[float, dict[int, tuple[int, int, int]]]],
        lengths: dict[int, tuple[int, int, int]],
        avg: tuple[float, float, float],
        params: tuple[float, ...],
    ) -> dict[int, tuple[float, int]]: ...


class _PyKernel:
    """Reference kernel. ``bm25f`` takes each query term's idf (computed by the
    caller) and postings ``{chunk: (tf_name, tf_path, tf_body)}`` and returns
    ``{chunk: (score, terms matched)}``; terms are summed in the order given."""

    name = "python"

    def tokenize(self, text: str) -> list[str]:
        out: list[str] = []
        for m in _WORD.finditer(text):
            out.extend(_word_terms(m.group()))
        return out

    def bm25f(
        self,
        terms: list[tuple[float, dict[int, tuple[int, int, int]]]],
        lengths: dict[int, tuple[int, int, int]],
        avg: tuple[float, float, float],
        params: tuple[float, ...],
    ) -> dict[int, tuple[float, int]]:
        k1, wn, wp, wb, bn, bp, bb = params
        an, ap, ab = avg
        out: dict[int, tuple[float, int]] = {}
        for idf, plist in terms:
            for cid, (tn, tp, tb) in plist.items():
                ln, lp, lb = lengths[cid]
                tfw = 0.0
                if tn:
                    tfw += wn * tn / (1.0 - bn + bn * ln / an)
                if tp:
                    tfw += wp * tp / (1.0 - bp + bp * lp / ap)
                if tb:
                    tfw += wb * tb / (1.0 - bb + bb * lb / ab)
                s = idf * tfw / (k1 + tfw)
                prev = out.get(cid)
                out[cid] = (s, 1) if prev is None else (prev[0] + s, prev[1] + 1)
        return out


_KERNEL: _Kernel | None = None


def _kernel() -> _Kernel:
    """The accelerator when installed and ABI-compatible, else the Python kernel.
    ``GRAPHLORE_SEARCH_KERNEL=python`` forces the reference."""
    global _KERNEL
    if _KERNEL is None:
        kernel: _Kernel = _PyKernel()
        if (config.env("SEARCH_KERNEL", "") or "").strip().lower() != "python":
            try:
                import graphlore_accel  # type: ignore[import-not-found]

                if getattr(graphlore_accel, "KERNEL_ABI", None) == _KERNEL_ABI:
                    kernel = graphlore_accel.Kernel()
            except Exception:  # noqa: BLE001 - a broken accelerator is no accelerator
                pass
        _KERNEL = kernel
    return _KERNEL


def _tokenize(text: str) -> list[str]:
    return _kernel().tokenize(text)


def _query_kind(query: str) -> str:
    """``symbol`` (a name: one word, or a few with ``_``, ``.``, ``::``, ``()`` or
    camel humps), ``code`` (a pasted snippet) or ``natural`` (a question)."""
    s = query.strip()
    if "\n" in s or len(re.findall(r"[{}();=<>\[\]]", s)) >= 3:
        return "code"
    words = s.split()
    if len(words) <= 1:
        return "symbol"
    if len(words) <= 3 and all(
        "_" in w or "." in w or "::" in w or "(" in w or re.search(r"[a-z][A-Z]", w)
        for w in words
    ):
        return "symbol"
    return "natural"


def _query_terms(query: str, kind: str) -> list[str]:
    """Distinct terms of the query in order; a question loses its stopwords
    (unless nothing else is left)."""
    words = [m.group() for m in _WORD.finditer(query)]
    if kind == "natural":
        kept = [w for w in words if w.lower() not in _STOPWORDS]
        words = kept or words
    seen: dict[str, None] = {}
    for w in words:
        for t in _word_terms(w):
            seen.setdefault(t, None)
    return list(seen)


def _path_weight(rel: str, wants_tests: bool) -> float:
    """The prior of a file: test code, examples, type stubs below the rest."""
    if not wants_tests and _is_test_path(rel):
        return _TEST_WEIGHT
    if any(p.lower() in _EXAMPLE_DIRS for p in rel.split("/")[:-1]):
        return _EXAMPLE_WEIGHT
    if rel.endswith(".d.ts"):
        return _STUB_WEIGHT
    return 1.0


def _symbol_key(query: str) -> str:
    """The name a symbol query asks for: its last ``.`` / ``::`` component, folded."""
    s = query.strip().rstrip("()").replace("::", ".")
    last = s.rsplit(".", 1)[-1]
    return re.sub(r"[^0-9a-z]", "", last.lower())


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Chunk:
    """A searchable piece of one file. ``start_line..end_line`` is the whole
    definition (for a ``definition`` chunk) even though a container indexes only
    the lines its children do not cover."""

    file_path: str
    start_line: int
    end_line: int
    def_line: int = 0
    qualname: str = ""
    kind: str = "window"  # definition | module | window

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1] if self.qualname else ""


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def _windows(
    rel: str, lines: list[str], lo: int, hi: int, kind: str
) -> list[tuple[Chunk, str]]:
    out = []
    start = lo
    while start <= hi:
        end = min(hi, start + _WINDOW_LINES - 1)
        body = lines[start - 1:end]
        if sum(1 for ln in body if ln.strip()) >= (1 if kind == "window" else _GAP_MIN_LINES):
            out.append((Chunk(rel, start, end, 0, "", kind), "\n".join(body)))
        start = end + 1
    return out


def _chunk_file(rel: str, src: bytes) -> tuple[list[tuple[Chunk, str]], bool]:
    """(chunks with their indexed text, whether the span engine read the file)."""
    text = src.decode("utf-8", "replace")
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return [], False
    spans = [
        (rs, min(end, n), dl, q) for rs, end, dl, q in _spans_for_file(rel)
        if 1 <= rs <= n and rs <= end
    ]
    if not spans:
        return _windows(rel, lines, 1, n, "window"), False
    order = sorted(set(spans), key=lambda s: (s[0], -s[1], s[2], s[3]))
    children: list[list[tuple[int, int]]] = [[] for _ in order]
    top: list[tuple[int, int]] = []
    stack: list[int] = []
    for i, (rs, end, _dl, _q) in enumerate(order):
        while stack and order[stack[-1]][1] < rs:
            stack.pop()
        if stack and order[stack[-1]][1] >= end:
            children[stack[-1]].append((rs, end))
        else:
            top.append((rs, end))
        stack.append(i)
    out: list[tuple[Chunk, str]] = []
    for i, (rs, end, dl, qual) in enumerate(order):
        own: list[int] = []
        cur = rs
        for crs, cend in sorted(children[i]):
            if crs > cur:
                own.extend(range(cur, crs))
            cur = max(cur, cend + 1)
        own.extend(range(cur, end + 1))
        body = "\n".join(lines[ln - 1] for ln in own)
        out.append((Chunk(rel, rs, end, dl, qual, "definition"), body))
    # Module-level code between the top-level definitions.
    covered = bytearray(n + 2)
    for rs, end in top:
        for ln in range(rs, end + 1):
            covered[ln] = 1
    ln = 1
    while ln <= n:
        if covered[ln]:
            ln += 1
            continue
        run_start = ln
        while ln <= n and not covered[ln]:
            ln += 1
        out.extend(_windows(rel, lines, run_start, ln - 1, "module"))
    return out, True


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class _FileEntry(NamedTuple):
    stamp: tuple[int, int]
    cids: tuple[int, ...]
    skipped: str  # "" when indexed, else why not (vendored / oversize / binary / capped)
    spans: bool


class _Overlay(NamedTuple):
    nodes: list[dict]
    postings: dict[str, dict[tuple[str, int], int]]
    lengths: dict[tuple[str, int], int]
    avg: float


def _tf_fields(
    name: list[str], path: list[str], body: list[str]
) -> dict[str, tuple[int, int, int]]:
    cn, cp, cb = Counter(name), Counter(path), Counter(body)
    terms = cn.keys() | cp.keys() | cb.keys()
    return {t: (cn.get(t, 0), cp.get(t, 0), cb.get(t, 0)) for t in terms}


def _idf(n: int, df: int) -> float:
    return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


class CodeIndex:
    """The built-in index of one project. Satisfies ``SemanticIndex``
    (``search`` / ``find_related``), plus ``hit_for_span`` and ``info``."""

    def __init__(self, root: str, spec: str) -> None:
        self.root = root
        self.spec = spec  # embedding model spec, or "off"
        self.lock = threading.RLock()
        self.files: dict[str, _FileEntry] = {}
        self.chunks: dict[int, Chunk] = {}
        self.fwd: dict[int, dict[str, tuple[int, int, int]]] = {}
        self.lengths: dict[int, tuple[int, int, int]] = {}
        self.postings: dict[str, dict[int, tuple[int, int, int]]] = {}
        self.totals = [0, 0, 0]
        self.by_def: dict[tuple[str, int], list[int]] = {}
        self.next_id = 0
        self.version = 0
        self.capped = False
        self.overlay: _Overlay | None = None
        self.dense: embed.VectorStore | None = None
        self.dense_reason = (
            "" if spec != "off" else "the dense tier is off (GRAPHLORE_EMBED_MODEL=off)"
        )
        self.ekeys: dict[int, bytes] = {}
        self._matrix: tuple[int, list[int], Any] | None = None
        self.last_embedded = 0

    # -- building ----------------------------------------------------------

    def _walk(self, extra: Iterable[str]) -> dict[str, tuple[int, int]]:
        root = Path(self.root)
        found: dict[str, tuple[int, int]] = {}
        self.capped = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and d not in _MENTION_SKIP_DIRS
                and d != config.OUT_DIR_NAME
            )
            for name in sorted(filenames):
                if os.path.splitext(name)[1].lower() not in _SEARCH_EXTS:
                    continue
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    continue  # a link may point outside the project: never follow it
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if len(found) >= _SEARCH_MAX_FILES:
                    self.capped = True
                    break
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                found[rel] = (st.st_mtime_ns, st.st_size)
        # Graph files the walk skipped (a build/ or hidden directory), confined.
        for f in extra:
            rel = _norm_relpath(f)
            if not rel or rel in found or len(found) >= _SEARCH_MAX_FILES:
                continue
            if os.path.splitext(rel)[1].lower() not in _SEARCH_EXTS:
                continue
            full_p = _resolve_in_project(rel)
            if full_p is None:
                continue
            try:
                st = full_p.stat()
            except OSError:
                continue
            if full_p.is_file():
                found[rel] = (st.st_mtime_ns, st.st_size)
        return found

    def _remove(self, rel: str) -> None:
        entry = self.files.pop(rel, None)
        if entry is None:
            return
        for cid in entry.cids:
            for term in self.fwd.pop(cid, {}):
                plist = self.postings.get(term)
                if plist is not None:
                    plist.pop(cid, None)
                    if not plist:
                        del self.postings[term]
            ln, lp, lb = self.lengths.pop(cid, (0, 0, 0))
            self.totals[0] -= ln
            self.totals[1] -= lp
            self.totals[2] -= lb
            chunk = self.chunks.pop(cid)
            if chunk.def_line:
                ids = self.by_def.get((chunk.file_path, chunk.def_line))
                if ids is not None:
                    ids.remove(cid)
                    if not ids:
                        del self.by_def[(chunk.file_path, chunk.def_line)]
            self.ekeys.pop(cid, None)

    def _add(self, rel: str, stamp: tuple[int, int], pending: list[tuple[int, bytes, str]]) -> None:
        def skip(why: str) -> None:
            self.files[rel] = _FileEntry(stamp, (), why, False)

        if stamp[1] > _SEARCH_MAX_FILE_BYTES:
            return skip("oversize")
        if _vendored_by_path(rel) or _minified_by_content(rel):
            return skip("vendored")
        full = _resolve_in_project(rel)
        if full is None:
            return skip("outside")
        try:
            src = full.read_bytes()
        except OSError:
            return skip("unreadable")
        if b"\0" in src[:2048]:
            return skip("binary")
        if len(self.chunks) >= _SEARCH_MAX_CHUNKS:
            self.capped = True
            return skip("capped")
        chunks, spans = _chunk_file(rel, src)
        kernel = _kernel()
        path_terms = kernel.tokenize(rel)
        cids = []
        for chunk, body in chunks:
            cid = self.next_id
            self.next_id += 1
            name_terms = kernel.tokenize(chunk.qualname) if chunk.qualname else []
            body_terms = kernel.tokenize(body)
            fields = _tf_fields(name_terms, path_terms, body_terms)
            self.fwd[cid] = fields
            for term, tfs in fields.items():
                self.postings.setdefault(term, {})[cid] = tfs
            lens = (len(name_terms), len(path_terms), len(body_terms))
            self.lengths[cid] = lens
            self.totals[0] += lens[0]
            self.totals[1] += lens[1]
            self.totals[2] += lens[2]
            self.chunks[cid] = chunk
            if chunk.def_line:
                self.by_def.setdefault((rel, chunk.def_line), []).append(cid)
            if self.spec != "off":
                etext = f"{rel} {chunk.qualname}\n{body}"
                key = embed.text_key(self.spec, etext)
                self.ekeys[cid] = key
                pending.append((cid, key, etext))
            cids.append(cid)
        self.files[rel] = _FileEntry(stamp, tuple(cids), "", spans)

    def refresh(self, nodes: list[dict] | None = None, edges: list[dict] | None = None) -> None:
        """Bring the index in line with the files on disk (and the graph overlay
        in line with ``nodes``): only changed files are re-read."""
        with self.lock:
            graph_files = {_node_file(n) for n in nodes} if nodes else set()
            found = self._walk(sorted(f for f in graph_files if f))
            changed = False
            for rel in [r for r in self.files if r not in found]:
                self._remove(rel)
                changed = True
            pending: list[tuple[int, bytes, str]] = []
            for rel in sorted(found):
                stamp = found[rel]
                entry = self.files.get(rel)
                if entry is not None and entry.stamp == stamp and entry.skipped != "capped":
                    continue
                self._remove(rel)
                self._add(rel, stamp, pending)
                changed = True
            if changed:
                self.version += 1
            self._refresh_dense(pending)
            if nodes is not None and (self.overlay is None or self.overlay.nodes is not nodes):
                self.overlay = _build_overlay(nodes, edges or [])

    def _refresh_dense(self, pending: list[tuple[int, bytes, str]]) -> None:
        self.last_embedded = 0
        if self.spec == "off":
            return
        if self.dense is None:
            if self.dense_reason:
                return
            if len(self.chunks) > embed.EMBED_MAX_CHUNKS:
                self.dense_reason = (
                    f"the dense tier is off: {len(self.chunks)} chunks is more than "
                    f"{embed.EMBED_MAX_CHUNKS}"
                )
                return
            model, why = embed._load_model(self.spec)
            if model is None:
                self.dense_reason = why
                return
            self.dense = embed.VectorStore(model, self.spec, self.root)
            # Texts are not kept: chunks indexed before this call are re-read
            # when the store (perhaps loaded from disk) has no vector for them.
            have = {cid for cid, _k, _t in pending}
            earlier = [(cid, k, "") for cid, k in self.ekeys.items() if cid not in have]
            pending = pending + self._texts_for(earlier)
        if len(self.chunks) > embed.EMBED_MAX_CHUNKS:
            self.dense = None
            self.dense_reason = (
                f"the dense tier is off: {len(self.chunks)} chunks is more than "
                f"{embed.EMBED_MAX_CHUNKS}"
            )
            return
        todo = [(k, t) for _cid, k, t in pending]
        if todo:
            try:
                _m, self.last_embedded = self.dense.vectors_for(
                    [k for k, _t in todo], [t for _k, t in todo]
                )
            except Exception as exc:  # noqa: BLE001 - a failing model degrades to BM25 only
                self.dense = None
                self.dense_reason = f"embedding failed ({type(exc).__name__}: {str(exc)[:200]})"
                return
        if self.last_embedded or pending:
            self.dense.save(set(self.ekeys.values()))

    def _texts_for(self, pending: list[tuple[int, bytes, str]]) -> list[tuple[int, bytes, str]]:
        """The embedded text of chunks whose vectors the store lacks (first dense
        load after a lexical build)."""
        assert self.dense is not None
        missing = {cid for cid, key, _t in pending if key not in self.dense._vecs}
        if not missing:
            return []
        out = []
        by_file: dict[str, list[int]] = {}
        for cid in missing:
            by_file.setdefault(self.chunks[cid].file_path, []).append(cid)
        for rel, cids in by_file.items():
            full = _resolve_in_project(rel)
            if full is None:
                continue
            try:
                chunks, _spans = _chunk_file(rel, full.read_bytes())
            except OSError:
                continue
            bodies = {(c.start_line, c.end_line, c.kind, c.qualname): b for c, b in chunks}
            for cid in cids:
                c = self.chunks[cid]
                body = bodies.get((c.start_line, c.end_line, c.kind, c.qualname))
                if body is not None:
                    out.append((cid, self.ekeys[cid], f"{rel} {c.qualname}\n{body}"))
        return out

    # -- querying ----------------------------------------------------------

    def _best(self, scores: dict[int, float], k: int) -> list[tuple[int, float]]:
        """The k best (chunk, score), best first, ties by (file, line) — exactly
        what a full sort gives, without sorting every match."""
        if len(scores) > 4 * k:
            cut = heapq.nlargest(k, scores.values())[-1]
            scores = {c: s for c, s in scores.items() if s >= cut}
        return sorted(scores.items(), key=lambda kv: (-kv[1], self._order(kv[0])))[:k]

    def _order(self, cid: int) -> tuple[str, int, int]:
        c = self.chunks[cid]
        return (c.file_path, c.start_line, c.end_line)

    def _avg(self) -> tuple[float, float, float]:
        n = max(1, len(self.chunks))
        return tuple(max(1.0, t / n) for t in self.totals)  # type: ignore[return-value]

    def _lexical(self, query: str, kind: str, overlay_weight: float) -> dict[int, float]:
        terms = _query_terms(query, kind)
        if kind == "code":
            terms = sorted(
                (t for t in terms if t in self.postings),
                key=lambda t: (len(self.postings[t]), t),
            )[:_QUERY_MAX_TERMS]
        n = len(self.chunks)
        plist = [
            (_idf(n, len(self.postings[t])), self.postings[t]) for t in terms if t in self.postings
        ]
        raw = _kernel().bm25f(
            plist, self.lengths, self._avg(),
            (_K1, _W_NAME, _W_PATH, _W_BODY, _B_NAME, _B_PATH, _B_BODY),
        )
        nterms = max(1, len(terms))
        scores: dict[int, float] = {}
        for cid, (s, matched) in raw.items():
            chunk = self.chunks[cid]
            if chunk.kind == "module":
                s *= _MODULE_WEIGHT
            if kind == "natural":
                s *= 0.5 + 0.5 * matched / nterms
            scores[cid] = s
        if overlay_weight and self.overlay is not None:
            for key, s in _overlay_scores(self.overlay, terms).items():
                for cid in self.by_def.get(key, ()):
                    scores[cid] = scores.get(cid, 0.0) + overlay_weight * s
        if kind == "symbol":
            for cid, boost in self._exact(query, scores).items():
                scores[cid] *= boost
        return scores

    def _exact(self, query: str, cids: Iterable[int]) -> dict[int, float]:
        """Chunks named like a symbol query: the boost each gets (whole qualified
        name, or its last component)."""
        want = _symbol_key(query)
        if not want:
            return {}
        whole = re.sub(r"[^0-9a-z.]", "", query.strip().rstrip("()").replace("::", ".").lower())
        out = {}
        for cid in cids:
            chunk = self.chunks[cid]
            if chunk.qualname and chunk.qualname.lower() == whole:
                out[cid] = _EXACT_QUALNAME_BOOST
            elif chunk.name and re.sub(r"[^0-9a-z]", "", chunk.name.lower()) == want:
                out[cid] = _EXACT_NAME_BOOST
        return out

    def _cohere(self, pool: list[tuple[int, float]], frac: float) -> list[tuple[int, float]]:
        """Each file's best chunk gains ``frac`` of the scores of the file's other
        chunks in the pool (sorted best first; the result is too)."""
        total: dict[str, float] = {}
        best: dict[str, int] = {}
        for cid, sc in pool:
            f = self.chunks[cid].file_path
            total[f] = total.get(f, 0.0) + sc
            best.setdefault(f, cid)
        out = [
            (cid, sc + frac * (total[self.chunks[cid].file_path] - sc)
             if best[self.chunks[cid].file_path] == cid else sc)
            for cid, sc in pool
        ]
        out.sort(key=lambda kv: (-kv[1], self._order(kv[0])))
        return out

    def _rerank(self, scored: list[tuple[int, float]], query: str) -> list[tuple[int, float]]:
        """The path prior, then the per-file decay, over the best candidates."""
        wants_tests = any(w.lower() in _TEST_WORDS for w in _WORD.findall(query))
        pool = [
            (cid, s * _path_weight(self.chunks[cid].file_path, wants_tests))
            for cid, s in scored[:_RERANK_DEPTH]
        ]
        pool.sort(key=lambda kv: (-kv[1], self._order(kv[0])))
        if _FILE_COHERENCE:
            pool = self._cohere(pool, _FILE_COHERENCE)
        seen: Counter[str] = Counter()
        out = []
        for cid, s in pool:
            f = self.chunks[cid].file_path
            out.append((cid, s * _FILE_DECAY ** seen[f]))
            seen[f] += 1
        out.sort(key=lambda kv: (-kv[1], self._order(kv[0])))
        return out

    def _dense_matrix(self) -> tuple[list[int], Any]:
        import numpy as np

        if self._matrix is not None and self._matrix[0] == self.version:
            return self._matrix[1], self._matrix[2]
        assert self.dense is not None
        vecs = self.dense._vecs
        cids = sorted(c for c in self.chunks if c in self.ekeys and self.ekeys[c] in vecs)
        mat = (
            np.stack([vecs[self.ekeys[c]] for c in cids]).astype(np.float32)
            if cids else None
        )
        self._matrix = (self.version, cids, mat)
        return cids, mat

    def search(
        self,
        query: str,
        top_k: int = 3,
        *,
        dense: bool | None = None,
        overlay_weight: float = _OVERLAY_WEIGHT,
    ) -> list[Hit]:
        """The ``top_k`` chunks for ``query``, best first. ``dense=False`` ranks
        lexically even when the dense tier is loaded (for comparisons)."""
        with self.lock:
            if top_k < 1 or not self.chunks:
                return []
            kind = _query_kind(query)
            lex = self._lexical(query, kind, overlay_weight)
            ranked = self._best(lex, max(top_k, _RERANK_DEPTH, _FUSION_DEPTH))
            use_dense = self.dense is not None and dense is not False
            if not use_dense:
                if _LEX_COHERENCE and kind != "symbol":
                    ranked = self._cohere(ranked[:_RERANK_DEPTH], _LEX_COHERENCE)
                ranked = self._rerank(ranked, query)
                return [Hit(self.chunks[c], round(s, 6)) for c, s in ranked[:top_k]]
            assert self.dense is not None
            cids, mat = self._dense_matrix()
            rows = embed.top_k(mat, self.dense.query(query), _FUSION_DEPTH)
            wl, wd = _FUSION_WEIGHTS[kind]
            fused: dict[int, float] = {}
            for r, (cid, _s) in enumerate(ranked[:_FUSION_DEPTH]):
                fused[cid] = wl / (_RRF_K + r + 1)
            for r, (row, _cos) in enumerate(rows):
                cid = cids[row]
                fused[cid] = fused.get(cid, 0.0) + wd / (_RRF_K + r + 1)
            if kind == "symbol" and _EXACT_FUSED_BONUS:
                for cid, boost in self._exact(query, list(fused)).items():
                    fused[cid] += _EXACT_FUSED_BONUS * boost / _EXACT_NAME_BOOST / (_RRF_K + 1)
            out = sorted(fused.items(), key=lambda kv: (-kv[1], self._order(kv[0])))
            out = self._rerank(out, query)
            return [Hit(self.chunks[c], round(s, 6)) for c, s in out[:top_k]]

    def _cid_of(self, hit: Any) -> int | None:
        c = getattr(hit, "chunk", None)
        if c is None:
            return None
        rel = _norm_relpath(getattr(c, "file_path", ""))
        try:
            sl, el = int(c.start_line), int(c.end_line)
        except (TypeError, ValueError, AttributeError):
            return None
        entry = self.files.get(rel)
        if entry is None:
            return None
        for cid in entry.cids:
            ch = self.chunks[cid]
            if ch.start_line == sl and ch.end_line == el:
                return cid
        return None

    def hit_for_span(self, file_path: str, start: int, end: int) -> Hit | None:
        """The chunk of a definition spanning ``start..end`` (exact anchor for the
        duplication scan): the definition chunk with those bounds, else the
        definition chunk that overlaps them most."""
        with self.lock:
            entry = self.files.get(_norm_relpath(file_path))
            if entry is None:
                return None
            best: tuple[int, int] | None = None
            for cid in entry.cids:
                ch = self.chunks[cid]
                if ch.kind != "definition":
                    continue
                if ch.start_line == start and ch.end_line == end:
                    return Hit(ch, 1.0)
                overlap = min(end, ch.end_line) - max(start, ch.start_line) + 1
                if overlap > 0 and (best is None or overlap > best[0]):
                    best = (overlap, cid)
            return Hit(self.chunks[best[1]], 1.0) if best else None

    def find_related(self, hit: Any, top_k: int = 8) -> list[Hit]:
        """Chunks most similar to ``hit``'s, best first, never the chunk itself
        or one overlapping it in the same file. Score: cosine in 0..1 (embedding
        vectors on the dense tier, tf-idf vectors on the lexical tier)."""
        with self.lock:
            cid = self._cid_of(hit)
            if cid is None or top_k < 1:
                return []
            me = self.chunks[cid]

            def far(other: Chunk) -> bool:
                return not (
                    other.file_path == me.file_path
                    and other.start_line <= me.end_line and other.end_line >= me.start_line
                )

            if self.dense is not None and cid in self.ekeys and self.ekeys[cid] in self.dense._vecs:
                cids, mat = self._dense_matrix()
                q = self.dense._vecs[self.ekeys[cid]].astype("float32")
                rows = embed.top_k(mat, q, top_k + 16)
                out = []
                for row, cos in rows:
                    other = cids[row]
                    if other == cid or not far(self.chunks[other]):
                        continue
                    out.append(Hit(self.chunks[other], round(min(1.0, max(0.0, cos)), 4)))
                    if len(out) >= top_k:
                        break
                return out
            return self._related_lexical(cid, top_k, far)

    def _tfidf(self, cid: int) -> dict[str, float]:
        n = len(self.chunks)
        out = {}
        # The body only: a renamed copy is still a copy.
        for t, (_tn, _tp, tb) in self.fwd[cid].items():
            if tb:
                out[t] = (1.0 + math.log(tb)) * _idf(n, len(self.postings[t]))
        return out

    def _related_lexical(self, cid: int, top_k: int, far: Any) -> list[Hit]:
        vec = self._tfidf(cid)
        if not vec:
            return []
        norm = math.sqrt(sum(w * w for w in vec.values()))
        heavy = sorted(vec, key=lambda t: (-vec[t], t))[:_RELATED_TERMS]
        partial: dict[int, float] = {}
        for t in heavy:
            for other in self.postings[t]:
                if other != cid:
                    partial[other] = partial.get(other, 0.0) + vec[t]
        cands = sorted(partial, key=lambda c: (-partial[c], self._order(c)))[:_RELATED_CANDIDATES]
        scored = []
        for other in cands:
            if not far(self.chunks[other]):
                continue
            ov = self._tfidf(other)
            onorm = math.sqrt(sum(w * w for w in ov.values()))
            if not onorm:
                continue
            dot = sum(w * ov[t] for t, w in vec.items() if t in ov)
            scored.append((min(1.0, dot / (norm * onorm)), other))
        scored.sort(key=lambda x: (-x[0], self._order(x[1])))
        return [Hit(self.chunks[o], round(s, 4)) for s, o in scored[:top_k]]

    # -- reporting ---------------------------------------------------------

    def mode(self) -> str:
        return "hybrid" if self.dense is not None else "bm25"

    def info(self) -> dict[str, Any]:
        with self.lock:
            skipped = Counter(e.skipped for e in self.files.values() if e.skipped)
            indexed = [e for e in self.files.values() if not e.skipped]
            out: dict[str, Any] = {
                "files": len(indexed),
                "chunks": len(self.chunks),
                "window_chunked_files": sum(1 for e in indexed if not e.spans and e.cids),
                "tiers": ["bm25", "dense"] if self.dense is not None else ["bm25"],
                "kernel": _kernel().name,
            }
            if self.dense is not None:
                out["model"] = self.spec
            if skipped:
                out["skipped"] = dict(sorted(skipped.items()))
            if self.capped:
                out["truncated"] = True
                out["hint"] = (
                    f"The index stops at {_SEARCH_MAX_FILES} files / {_SEARCH_MAX_CHUNKS} "
                    "chunks; files past that are not searched."
                )
            return out


def _build_overlay(nodes: list[dict], edges: list[dict]) -> _Overlay:
    """BM25 documents keyed by (file, line) of each code node: its docstring and
    the names of what it calls and what calls it."""
    labels = {_node_id(n): _node_label(n) for n in nodes}
    callees: dict[str, list[str]] = {}
    callers: dict[str, list[str]] = {}
    for e in edges:
        if _rel(e) not in _CALL_RELATIONS:
            continue
        s, t = _edge_ends(e)
        if s in labels and t in labels:
            callees.setdefault(s, []).append(labels[t])
            callers.setdefault(t, []).append(labels[s])
    kernel = _kernel()
    postings: dict[str, dict[tuple[str, int], int]] = {}
    lengths: dict[tuple[str, int], int] = {}
    for n in nodes:
        rel = _norm_relpath(_node_file(n))
        try:
            line = int(_node_line(n))
        except (TypeError, ValueError):
            continue
        if not rel:
            continue
        nid = _node_id(n)
        doc = n.get("doc") or n.get("docstring") or n.get("summary") or n.get("description") or ""
        words = [str(doc)]
        words += callees.get(nid, [])[:_OVERLAY_NEIGHBOURS]
        words += callers.get(nid, [])[:_OVERLAY_NEIGHBOURS]
        terms = kernel.tokenize(" ".join(words))
        if not terms:
            continue
        key = (rel, line)
        lengths[key] = lengths.get(key, 0) + len(terms)
        for t, c in Counter(terms).items():
            plist = postings.setdefault(t, {})
            plist[key] = plist.get(key, 0) + c
    avg = max(1.0, sum(lengths.values()) / max(1, len(lengths)))
    return _Overlay(nodes, postings, lengths, avg)


def _overlay_scores(ov: _Overlay, terms: list[str]) -> dict[tuple[str, int], float]:
    n = len(ov.lengths)
    out: dict[tuple[str, int], float] = {}
    for t in terms:
        plist = ov.postings.get(t)
        if not plist:
            continue
        idf = _idf(n, len(plist))
        for key, tf in plist.items():
            norm = tf / (1.0 - 0.5 + 0.5 * ov.lengths[key] / ov.avg)
            out[key] = out.get(key, 0.0) + idf * norm / (_K1 + norm)
    return out


# ---------------------------------------------------------------------------
# The per-project cache
# ---------------------------------------------------------------------------

_INDEX_CACHE: dict[tuple[str, str], CodeIndex] = {}
_CACHE_LOCK = threading.Lock()


def index_for_project(
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    dense: bool | None = None,
) -> CodeIndex | None:
    """The built-in index of ``config.PROJECT_DIR``, refreshed against the disk.

    ``dense``: None follows ``GRAPHLORE_EMBED_MODEL`` (on unless ``off``), True
    asks for the dense tier, False builds a lexical index. None when the project
    has no searchable source file.
    """
    root = str(config.PROJECT_DIR.resolve())
    if dense is False or (dense is None and not embed.dense_wanted()):
        spec = "off"
    else:
        spec = embed.model_spec() if embed.dense_wanted() else embed.DEFAULT_MODEL
    key = (root, spec)
    with _CACHE_LOCK:
        idx = _INDEX_CACHE.get(key)
        if idx is None:
            if len(_INDEX_CACHE) >= _INDEX_CACHE_MAX:
                _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)), None)  # FIFO
            idx = CodeIndex(root, spec)
            _INDEX_CACHE[key] = idx
    idx.refresh(nodes, edges)
    return idx if idx.chunks else None
