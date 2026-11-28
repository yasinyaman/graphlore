"""A SCIP index read with the standard library: what a compiler-grade indexer
says each name refers to.

SCIP (https://github.com/sourcegraph/scip) is the index format of
``scip-python``, ``scip-typescript``, ``scip-java``, ``rust-analyzer scip``,
``scip-go``, ``scip-clang``, ``scip-dotnet`` and others: per document, every
occurrence of a symbol with its range and roles (definition, import, read,
write). graphlore reads it to check its own graph against a compiler's answer
(``resolve._precise``); it never runs an indexer and never reaches the network.

Which files: ``GRAPHLORE_SCIP`` — ``auto`` (the default: ``index.scip`` at the
project root or in the output directory, when there is one), ``off``, or a
comma-separated list of paths inside the project. The protobuf is decoded here
(the field numbers are ``scip.proto``'s), so no ``protobuf`` package is needed;
a file that is not an index is skipped and named in ``errors``.

Imports only ``config``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config

# SymbolRole bits (scip.proto)
DEFINITION = 0x1
IMPORT = 0x2
WRITE = 0x4
READ = 0x8
GENERATED = 0x10
TEST = 0x20
FORWARD_DEFINITION = 0x40

DEFAULT_NAME = "index.scip"
# an index larger than this is not read: decoding it in Python would take minutes
MAX_BYTES = 512 * 1024 * 1024


@dataclass
class Occurrence:
    """One use of a symbol: 1-based line, 0-based characters."""
    file: str
    line: int
    char: int
    end_char: int
    symbol: str
    roles: int


@dataclass
class Index:
    """What the index files say, paths relative to the project directory."""
    sources: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    documents: int = 0
    # symbol -> (file, 1-based line) of its first definition in the project
    defs: dict[str, tuple[str, int]] = field(default_factory=dict)
    # symbol -> every definition, for a symbol defined more than once: a
    # partial type, or namesakes the indexer gives one symbol (scip-dotnet
    # writes `Maybe<T>` and `Maybe` as `Maybe#`, a namespace by its last part)
    more_defs: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    refs: list[Occurrence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # uses left out because the source text at their range is not the name of
    # their symbol: an indexer's slip (scip-python binds a name re-exported by a
    # package to a neighbour of it), an alias (`import numpy as np`), or a file
    # that changed since the index was made
    mismatched: int = 0
    _by_descriptor: dict[str, list[tuple[str, tuple[str, int]]]] | None = field(
        default=None, repr=False)

    def def_of(self, symbol: str) -> tuple[str, int] | None:
        """Where a symbol is defined in the project: by the symbol itself, else
        by its descriptors alone. An indexer may name one definition two ways:
        the project's own package (``click <commit>``, and a module named from
        its path, ``src.click.utils``) where it is defined, the installed copy
        (``click 8.5.0``, ``click.utils``) where a test imports it. The module
        may be longer on the definition's side; one candidate only."""
        got = self.defs.get(symbol)
        if got is not None:
            return got
        if self._by_descriptor is None:
            self._by_descriptor = {}
            for sym, where in self.defs.items():
                module, rest = _module_and_rest(descriptor(sym))
                if rest:
                    self._by_descriptor.setdefault(rest, []).append((module, where))
        module, rest = _module_and_rest(descriptor(symbol))
        if not rest:
            return None
        cands = {where for m, where in self._by_descriptor.get(rest, [])
                 if m == module or (module and m.endswith("." + module))}
        return next(iter(cands)) if len(cands) == 1 else None

    def defs_of(self, symbol: str) -> list[tuple[str, int]]:
        """Every definition of a symbol in the project (``def_of`` first). A
        constructor the source does not declare (C#'s implicit one, which
        scip-dotnet names at `[Mark]` and `new T()`) is its type's."""
        first = self.def_of(symbol)
        if first is None:
            owner = constructed_type(symbol)
            return self.defs_of(owner) if owner else []
        return self.more_defs.get(symbol) or [first]


# --- protobuf ------------------------------------------------------------------

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def _fields(buf: bytes):
    """(field number, wire type, value) of a message: an int for a varint, the
    bytes of a length-delimited field; fixed-width fields are skipped."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _varint(buf, i)
            yield num, 0, v
        elif wire == 2:
            ln, i = _varint(buf, i)
            if i + ln > n:
                raise ValueError("truncated field")
            yield num, 2, buf[i:i + ln]
            i += ln
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        else:
            raise ValueError(f"wire type {wire}")


def _int32(v: int) -> int:
    v &= 0xFFFFFFFFFFFFFFFF
    return v - (1 << 64) if v >= 1 << 63 else v


def _packed(buf: bytes) -> list[int]:
    out, i = [], 0
    while i < len(buf):
        v, i = _varint(buf, i)
        out.append(_int32(v))
    return out


def _range(occ_fields: list[tuple[int, int, object]]) -> tuple[int, int, int] | None:
    """(0-based start line, start char, end char on that line) of an occurrence:
    the typed ranges first (fields 8, 9), then the packed one (field 1)."""
    legacy: list[int] = []
    for num, wire, v in occ_fields:
        if num in (8, 9) and wire == 2:
            parts = {k: _int32(x) for k, w, x in _fields(v) if w == 0}  # type: ignore[arg-type]
            start = parts.get(1, 0)
            char = parts.get(2, 0)
            end = parts.get(3, char) if num == 8 else parts.get(4, char)
            return start, char, end
        if num == 1:
            if wire == 2:
                legacy += _packed(v)  # type: ignore[arg-type]
            elif wire == 0:
                legacy.append(_int32(v))  # type: ignore[arg-type]
    if len(legacy) == 3:
        return legacy[0], legacy[1], legacy[2]
    if len(legacy) == 4:
        return legacy[0], legacy[1], legacy[3] if legacy[2] == legacy[0] else legacy[1]
    return None


def _str(v: object) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else ""


def _root_prefix(project_root: str, project: Path) -> str | None:
    """The index's project root as a path prefix inside the project ("" when it
    is the project itself; None when it is neither inside it nor above it)."""
    if not project_root:
        return ""
    parsed = urlparse(project_root)
    raw = unquote(parsed.path) if parsed.scheme == "file" else project_root
    try:
        root = Path(raw).resolve()
        here = project.resolve()
    except OSError:
        return None
    if root == here:
        return ""
    try:
        return root.relative_to(here).as_posix() + "/"
    except ValueError:
        pass
    try:
        # the index was made above the project: its paths carry the project's
        return "-" + here.relative_to(root).as_posix() + "/"
    except ValueError:
        return None


def read_index(data: bytes, project: Path, into: Index, source: str) -> None:
    """Add what one encoded ``Index`` message says to ``into``."""
    prefix = ""
    docs: list[bytes] = []
    for num, wire, v in _fields(data):
        if num == 1 and wire == 2:  # Metadata
            for m, w, x in _fields(v):  # type: ignore[arg-type]
                if m == 3 and w == 2:
                    got = _root_prefix(_str(x), project)
                    if got is None:
                        # made in another checkout (a CI container's /src): its
                        # paths are read as the project's, where such a file is
                        into.errors.append(
                            f"{source}: made in another directory ({_str(x)}); its paths are "
                            "read as this project's, and a document no file here matches "
                            "is left out")
                        prefix = "?"
                    else:
                        prefix = got
                elif m == 2 and w == 2:
                    name = next((_str(y) for k, wy, y in _fields(x) if k == 1 and wy == 2), "")  # type: ignore[arg-type]
                    if name and name not in into.tools:
                        into.tools.append(name)
        elif num == 2 and wire == 2:
            docs.append(v)  # type: ignore[arg-type]
    for doc in docs:
        _read_document(doc, prefix, into, project)
    into.sources.append(source)


def _read_document(doc: bytes, prefix: str, into: Index, project: Path) -> None:
    rel = ""
    occs: list[bytes] = []
    encoding = 0
    for num, wire, v in _fields(doc):
        if num == 1 and wire == 2:
            rel = _str(v)
        elif num == 2 and wire == 2:
            occs.append(v)  # type: ignore[arg-type]
        elif num == 6 and wire == 0:
            encoding = int(v)  # type: ignore[call-overload]
    rel = rel.lstrip("./").replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        return
    if prefix == "?":
        if not (project / rel).is_file():
            return
    elif prefix.startswith("-"):
        above = prefix[1:]
        if not rel.startswith(above):
            return  # a document of the indexed tree outside this project
        rel = rel[len(above):]
    else:
        rel = prefix + rel
    into.documents += 1
    lines = _lines(project / rel)
    for occ in occs:
        fs = list(_fields(occ))
        rng = _range(fs)
        if rng is None:
            continue
        symbol = next((_str(v) for n, w, v in fs if n == 2 and w == 2), "")
        if not symbol or symbol.startswith("local "):
            continue
        roles = next((int(v) for n, w, v in fs if n == 3 and w == 0), 0)  # type: ignore[call-overload]
        line, char, end = rng
        if roles & DEFINITION:
            where = (rel, line + 1)
            first = into.defs.setdefault(symbol, where)
            if first != where:
                more = into.more_defs.setdefault(symbol, [first])
                if where not in more:
                    more.append(where)
        elif lines is not None and not _names(lines, line, char, end, encoding, symbol):
            into.mismatched += 1
        else:
            into.refs.append(Occurrence(rel, line + 1, char, end, symbol, roles))


def _lines(path: Path) -> list[str] | None:
    """A source file's lines, split on ``\n`` only (as an indexer counts them;
    ``str.splitlines`` also splits on form feeds and line separators)."""
    try:
        # a byte-order mark is no character of the first line (`utf-8-sig`)
        return path.read_text(encoding="utf-8-sig", errors="replace").split("\n")
    except OSError:
        return None


def _slice(text: str, start: int, end: int, encoding: int) -> set[str]:
    """What ``[start, end)`` of a line is under the document's position
    encoding (1 UTF-8, 2 UTF-16, 3 UTF-32 code units); every reading when the
    document does not say."""
    out = set()
    if encoding in (0, 3):
        out.add(text[start:end])
    for enc, code in (("utf-8", 1), ("utf-16-le", 2)):
        if encoding in (0, code):
            raw = text.encode(enc, "replace")
            unit = 2 if code == 2 else 1
            out.add(raw[start * unit:end * unit].decode(enc, "replace"))
    return out


def _names(lines: list[str], line: int, char: int, end: int, encoding: int,
           symbol: str) -> bool:
    """Whether the source writes the symbol's name where the index says it
    does. A symbol whose name cannot be read is kept."""
    name = bare_name(symbol)
    if not name or line >= len(lines) or end <= char:
        return True
    text = _slice(lines[line], char, end, encoding)
    # (`x["name"]`: a member written as a string)
    if name in text or any(t[:1] in "\"'`" and t.strip("\"'`") == name for t in text):
        return True
    # Ruby: `X.new(…)` calls `initialize`; a writer `timeout=` is written
    # `x.timeout = v`
    if (name == "initialize" and "new" in text) or (
            len(name) > 1 and name.endswith("=") and name[:-1] in text):
        return True
    # a C# attribute is written without its suffix: `[Option]` is `OptionAttribute`
    if name.endswith("Attribute") and name[:-len("Attribute")] in text:
        return True
    # rust-analyzer's crate (`rust-analyzer cargo grep-regex 0.1.14 crate/`)
    # is written by its package's name, or `crate` inside it
    parts = symbol.split(" ", 4)
    if len(parts) == 5 and parts[4].rstrip() == "crate/":
        return any(t in ("crate", parts[2], parts[2].replace("-", "_")) for t in text)
    # an import of a package names its whole path (Go: `import "net/http"`),
    # or a module by what the importer writes (TypeScript: `from "./util.js"`)
    m = _LAST_DESCRIPTOR.search(symbol.rstrip())
    if not m or m.group("kind") != "/":
        return False
    return m.group("q") in text or any(t[:1] in "\"'" and t[-1:] == t[:1] for t in text)


# --- symbols ---------------------------------------------------------------------

_LAST_DESCRIPTOR = re.compile(
    r"(?:`(?P<q>[^`]+)`|(?P<n>[^\s`/#.:!()\[\]]+))"
    r"(?P<kind>\([^)]*\)\.|[#.:!/]|\([^)]*\)|\[[^\]]*\])$"
)


_MAJOR_VERSION = re.compile(r"v[0-9]+")
# scip-dotnet's, scip-typescript's and scip-java's names of a constructor
_CONSTRUCTORS = (".ctor", ".cctor", "<constructor>", "<init>")
# scip-typescript's accessors: `<get>body` and `<set>body` are written `body`
_ACCESSORS = ("<get>", "<set>")


def constructed_type(symbol: str) -> str | None:
    """The type a constructor symbol builds (``…/R#`.ctor`().`` is
    ``…/R#``), None for any other symbol."""
    m = _LAST_DESCRIPTOR.search(symbol.rstrip())
    if not m or (m.group("q") or m.group("n")) not in _CONSTRUCTORS \
            or not m.group("kind").startswith("("):
        return None
    owner = symbol.rstrip()[:m.start()]
    return owner if owner.endswith("#") else None


def descriptor(symbol: str) -> str:
    """A global symbol without its scheme, manager, package and version:
    ``scip-python python click 8.5.0 `click.utils`/echo().`` is
    ``` `click.utils`/echo().``` ("" for a local symbol)."""
    parts = symbol.split(" ", 4)
    return parts[4] if len(parts) == 5 and not symbol.startswith("local ") else ""


def _module_and_rest(desc: str) -> tuple[str, str]:
    """`` `click.utils`/echo().`` -> (``click.utils``, ``echo().``); a
    descriptor with no namespace first is all rest."""
    if desc.startswith("`"):
        end = desc.find("`/", 1)
        if end > 0:
            return desc[1:end], desc[end + 2:]
    head, sep, rest = desc.partition("/")
    if sep and not any(c in head for c in "#.():![]`"):
        return head, rest
    return "", desc


def bare_name(symbol: str) -> str:
    """The last name a symbol's descriptors write: ``…/Client#send().`` is
    ``send``, ``…/Client#`` ``Client``; "" for a parameter or a type
    parameter (local to their definition) or a symbol it cannot read."""
    m = _LAST_DESCRIPTOR.search(symbol.rstrip())
    if not m:
        return ""
    kind = m.group("kind")
    if kind.startswith("(") and not kind.endswith(".") or kind.startswith("["):
        return ""
    name = m.group("q") or m.group("n") or ""
    if kind == "/" and "/" in name:
        # a package named by its import path (scip-go: `` `net/http`/ ``) is
        # written by its last segment, the one before a major version
        # (`github.com/go-chi/chi/v5` is `chi`)
        segs = [s for s in name.split("/") if s]
        if len(segs) > 1 and _MAJOR_VERSION.fullmatch(segs[-1]):
            segs.pop()
        name = segs[-1] if segs else ""
    if name in _CONSTRUCTORS and kind.startswith("("):
        # a constructor (scip-dotnet: ``RestRequest#`.ctor`(+1).``) is named
        # as its type, the way the source writes it and the graph names it
        inner = _LAST_DESCRIPTOR.search(symbol.rstrip()[:m.start()])
        return (inner.group("q") or inner.group("n") or "") if inner else ""
    if name.startswith(_ACCESSORS):
        name = name[len("<get>"):]
    if kind == ":" and name == "__init__":
        # scip-python's module: `` `pkg.sub`/__init__: `` is the module ``sub``
        before = symbol.rstrip()[:m.start()].rstrip("/")
        inner = _LAST_DESCRIPTOR.search(before + "/") if before else None
        if inner:
            name = (inner.group("q") or inner.group("n") or "").rsplit(".", 1)[-1]
        else:
            name = before.rsplit(" ", 1)[-1].strip("`").rsplit(".", 1)[-1]
    return name


# --- which files ------------------------------------------------------------------

def setting() -> str:
    return (config.env("SCIP", "auto") or "auto").strip()


def index_files(project: Path | None = None) -> list[Path]:
    """The index files ``GRAPHLORE_SCIP`` names, inside the project only."""
    root = Path(project or config.PROJECT_DIR).resolve()
    raw = setting()
    if raw.lower() in ("off", "0", "false", "no", "none", ""):
        return []
    if raw.lower() == "auto":
        cands = [root / DEFAULT_NAME, *(root / n / DEFAULT_NAME for n in config.out_dir_names())]
    else:
        cands = [root / p.strip() for p in raw.split(",") if p.strip()]
    out: list[Path] = []
    for c in cands:
        try:
            r = c.resolve()
            r.relative_to(root)
        except (OSError, ValueError):
            continue
        if r.is_file() and r not in out:
            out.append(r)
    return out


_CACHE: dict[tuple, Index] = {}
_CACHE_MAX = 4


def load(project: Path | None = None) -> Index | None:
    """The index the project's SCIP files make, cached on their paths, sizes and
    modification times; None when there is none."""
    root = Path(project or config.PROJECT_DIR).resolve()
    files = index_files(root)
    if not files:
        return None
    key: tuple = (str(root),)
    for f in files:
        st = f.stat()
        key += ((str(f), st.st_size, st.st_mtime_ns),)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    out = Index()
    for f in files:
        name = f.relative_to(root).as_posix()
        if f.stat().st_size > MAX_BYTES:
            out.errors.append(f"{name}: larger than {MAX_BYTES // (1024 * 1024)} MB, not read")
            continue
        try:
            read_index(f.read_bytes(), root, out, name)
        except (OSError, ValueError, IndexError) as e:
            out.errors.append(f"{name}: not a SCIP index ({e})")
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[key] = out
    return out
