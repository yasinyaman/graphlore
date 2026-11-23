"""Review analyses over the graph: unreferenced code, same-named symbols, layer rules.

Each is exact and needs no embedding model. They read the graph's own edges and,
where a claim needs checking against the source (a decorator that registers a
function, a name mentioned in a template or a config file), the files under
``config.PROJECT_DIR`` through the confined readers of :mod:`graphlore.spans`.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
from collections import Counter
from typing import Any

from . import config
from .graph import (
    _IMPORT_RELATIONS,
    _NON_DEPENDENCY_RELATIONS,
    _bare_name,
    _edge_ends,
    _file_import_edges,
    _is_file_hub_node,
    _is_test_path,
    _lang_family,
    _node_file,
    _node_id,
    _node_label,
    _node_line,
    _rel,
)
from .imports import ImportChecker
from .scope import _clean_pattern, _path_matches, _vendored_by_path
from .spans import (
    _cached_file_analysis,
    _is_test_node,
    _is_ts_symbol,
    _norm_relpath,
    _resolve_in_project,
    _span_of_definition,
    _spans_for_file,
    _ts_bound_function,
    _ts_parser_for,
    _ts_skeleton,
)

# Extensions whose nodes are source definitions (not config keys or doc headings).
_CODE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts",
    ".go", ".java", ".kt", ".kts", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cxx", ".hxx", ".cs", ".rb", ".php", ".swift", ".scala", ".m", ".mm",
    ".lua", ".zig", ".ex", ".exs",
})
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
# A decorator / annotation / attribute at the start of a line: `@app.route`,
# Java `@Test`, C# `[Fact]`, Rust `#[test]`.
_DECORATOR_LINE = re.compile(r"^\s*(?:@\s*|#\s*\[\s*|\[\s*)([A-Za-z_][\w.:]*)")
# How far below the first line of a definition its name may sit: Java, C#, Swift
# and Kotlin count the annotations as part of the declaration.
_DECORATOR_REACH = 12
# An identifier, and whether it is reached as an attribute (`obj.name`).
_MENTION = re.compile(r"(\.\s*)?([A-Za-z_$][A-Za-z0-9_$]*)")


def _int_line(n: dict) -> int:
    try:
        return int(_node_line(n))
    except (TypeError, ValueError):
        return 0


class _Definitions:
    """Which nodes are definitions, of what kind, and how they nest.

    ``kind`` is "function", "method" or "class". graphify >= 0.9 marks callables
    (``_callable`` / ``_callable_class``); older graphs are read from the label
    convention (``name()``, ``.method()``) and from containment — a class is an
    identifier-labelled code node that a file contains or that has methods.
    """

    def __init__(self, nodes: list[dict], edges: list[dict]) -> None:
        self.byid: dict[str, dict] = {_node_id(n): n for n in nodes}
        self.parent: dict[str, str] = {}
        self.members: dict[str, list[str]] = {}
        self.incoming: dict[str, list[tuple[str, dict]]] = {}
        has_methods: set[str] = set()
        contained: set[str] = set()
        for e in edges:
            s, t = _edge_ends(e)
            rel = _rel(e)
            if rel == "method":
                has_methods.add(s)
                self.parent.setdefault(t, s)
                self.members.setdefault(s, []).append(t)
            elif rel == "contains":
                contained.add(t)
                self.parent.setdefault(t, s)
            elif s != t and rel not in _NON_DEPENDENCY_RELATIONS:
                self.incoming.setdefault(t, []).append((s, e))
        self._classes: dict[str, list[str]] | None = None
        self._member_names: dict[str, frozenset[str]] = {}
        # _hook_base's answers, per (method name, bases, depth), for this graph
        self.hooks: dict[tuple[str, tuple[str, ...], int], str | None] = {}
        self.kind: dict[str, str] = {}
        for nid, n in self.byid.items():
            k = self._kind(n, nid in has_methods, nid in contained)
            if k:
                self.kind[nid] = k

    @staticmethod
    def _kind(n: dict, has_methods: bool, contained: bool) -> str:
        f = _node_file(n)
        dot = f.rfind(".")
        if not f or dot < 0 or f[dot:].lower() not in _CODE_EXTS or _is_file_hub_node(n):
            return ""
        if str(n.get("file_type") or "code").lower() != "code":
            return ""
        label = _node_label(n)
        # the native engine says what a node is: a variable, a field or a
        # constant is no class, however it hangs off its file
        if str(n.get("kind", "")).lower() in ("variable", "property", "constant", "heading"):
            return ""
        if n.get("_callable_class"):
            return "class"
        if label.endswith("()"):
            return "method" if label.startswith(".") else "function"
        if n.get("_callable"):
            return "function"
        if (has_methods or contained) and _IDENT.fullmatch(label):
            return "class"
        return ""

    def classes_named(self, name: str) -> list[str]:
        """Ids of the project's class definitions with this bare name."""
        if self._classes is None:
            self._classes = {}
            for nid, kind in self.kind.items():
                if kind == "class":
                    self._classes.setdefault(
                        _bare_name(_node_label(self.byid[nid])), []
                    ).append(nid)
        return self._classes.get(name, [])

    def member_names(self, nid: str) -> frozenset[str]:
        """Bare names of the members (``method`` edges) of ``nid``."""
        got = self._member_names.get(nid)
        if got is None:
            got = frozenset(_bare_name(_node_label(self.byid[m]))
                            for m in self.members.get(nid, ()) if m in self.byid)
            self._member_names[nid] = got
        return got

    def is_top_level(self, nid: str) -> bool:
        """Defined directly in a file (not a method, not nested in a function)."""
        parent = self.byid.get(self.parent.get(nid, ""))
        return parent is None or _is_file_hub_node(parent)

    def referencing_files(self, nid: str, relations: frozenset[str] | None = None) -> set[str]:
        """Other files with an edge into ``nid`` (or, for a class, into its members)."""
        own = _node_file(self.byid[nid])
        family = {nid, *self.members.get(nid, ())}
        out: set[str] = set()
        for target in family:
            for src, e in self.incoming.get(target, ()):
                if src in family or (relations is not None and _rel(e) not in relations):
                    continue
                n = self.byid.get(src)
                f = (_node_file(n) if n else "") or str(e.get("source_file") or "")
                if f and f != own:
                    out.add(f)
        return out


# ---------------------------------------------------------------------------
# Unreferenced code
# ---------------------------------------------------------------------------

# Decorators that change how a function behaves but hand it to no one: a function
# carrying only these is still dead when nothing references it. Compared against
# the last dotted segment, lower-cased. Any OTHER decorator is taken to register
# the function somewhere the graph cannot see (a route table, a CLI group, a
# validator list, a pytest fixture registry).
_NEUTRAL_DECORATORS = frozenset({
    "staticmethod", "classmethod", "wraps", "lru_cache", "cache", "cached",
    "contextmanager", "asynccontextmanager", "dataclass", "total_ordering",
    "overload", "final", "override", "abstractmethod", "deprecated",
    "no_type_check", "suppresswarnings", "safevarargs", "functionalinterface",
    "inline", "noinline", "nonnull", "nullable", "notnull",
    # Rust / C# / Swift / Kotlin attributes that say how to compile or lint a
    # definition, not that something will call it
    "must_use", "derive", "doc", "allow", "warn", "deny", "cfg", "cfg_attr", "repr",
    "non_exhaustive", "track_caller", "obsolete", "methodimpl", "attributeusage",
    "serializable", "debuggerdisplay", "available", "discardableresult", "sendable",
    "jvmstatic", "jvmoverloads", "jvmfield", "jvmname", "throws", "suppress", "optin",
})
_MENTION_SKIP_DIRS = frozenset({
    "node_modules", "venv", "env", "__pycache__", "dist", "build", "target",
    "site-packages",
})
_MENTION_MAX_BYTES = 1_000_000
_MENTION_MAX_FILES = 20_000


# The line opening a class: `class V(ast.NodeVisitor):`, `class Handler extends
# Base {`, `class Repo : IRepo`. Group 1 is the class name, group 2 what follows it.
_CLASS_LINE = re.compile(
    r"^\s*(?:(?:export|public|private|protected|abstract|final|default|static|open|"
    r"sealed|data)\s+)*class\s+([\w$]+)\s*(?:<[^>]*>)?\s*(.*)$"
)
_INHERITANCE_RELATIONS = frozenset({"inherits", "extends", "implements", "mixes_in"})
# Bases that never call back into a subclass's ordinary methods: markers, data
# containers and the exception hierarchy. A method under only these is not a hook.
_NON_HOOK_BASES = frozenset({
    "ABC", "ABCMeta", "object", "Object", "Protocol", "Generic", "BaseModel", "Enum",
    "IntEnum", "StrEnum", "Flag", "IntFlag", "NamedTuple", "TypedDict", "Exception",
    "BaseException", "Any",
})
_NON_HOOK_SUFFIXES = ("Error", "Exception", "Warning")


def _class_bases(text: str) -> list[str]:
    """Base-class names on a class line, last dotted segment, generics stripped.

    ``class X(A, b.C[T], metaclass=M):`` -> [A, C]; ``class X extends A implements
    I, J {`` -> [A, I, J]; ``class X : A(), B`` -> [A, B]. [] when it names none.
    """
    m = _CLASS_LINE.match(text)
    if not m:
        return []
    rest = m.group(2).strip()
    raw: list[str] = []
    if rest.startswith("("):
        depth, cur = 0, ""
        for ch in rest[1:]:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            if ch == "," and depth == 0:
                raw.append(cur)
                cur = ""
            else:
                cur += ch
        raw.append(cur)
        raw = [r for r in raw if "=" not in r]  # metaclass=..., total=...
    elif rest.startswith(("extends", "implements")):
        for part in re.split(r"\bextends\b|\bimplements\b", rest.split("{")[0]):
            raw += part.split(",")
    elif rest.startswith(":"):
        raw = rest[1:].split("{")[0].split(",")
    out: list[str] = []
    for r in raw:
        name = re.split(r"[\[<(]", r.strip())[0].strip().split()[-1:] or [""]
        last = name[0].rsplit(".", 1)[-1]
        if last and _IDENT.fullmatch(last):
            out.append(last)
    return out


def _is_non_hook_base(name: str) -> bool:
    return name in _NON_HOOK_BASES or name.endswith(_NON_HOOK_SUFFIXES)


def _enclosing_class_bases(file: str, line: int, lines: _SourceLines) -> list[str] | None:
    """Bases of the class the definition at ``line`` is written directly inside.

    None when it is not inside a class. Read from the source, because graphify
    gives no node to a class nested in a function — exactly where visitor and
    event-handler subclasses tend to live.
    """
    at = lines.spans_at(file, line)
    if not at:
        return None
    own = at[0]
    spans = lines.spans(file)
    outer = [s for s in spans if s[0] <= own[0] and s[1] >= own[1] and s is not own]
    if not outer:
        return None
    parent = min(outer, key=lambda s: s[1] - s[0])
    text = lines.get(file)
    if not 0 < parent[2] <= len(text) or not _CLASS_LINE.match(text[parent[2] - 1]):
        return None
    return _class_bases(text[parent[2] - 1])


def _hook_base(
    name: str, bases: list[str], defs: _Definitions, lines: _SourceLines, depth: int = 0
) -> str | None:
    """The base class that may call a subclass's method ``name``, if any.

    A base outside the project (a framework class such as ``ast.NodeVisitor``)
    may: its code is not here to read. A base inside the project does only if it
    defines or mentions a method of that name, or one of ITS bases may. Marker
    and data bases (``ABC``, ``Protocol``, ``BaseModel``, exceptions) never do.
    """
    # Memoized per call (``defs`` is): a popular base (Django's ``TestCase``,
    # ``Model``) is asked about once per method of every subclass, and the same
    # base chain is reached again through each class that shares a name.
    key = (name, tuple(bases), depth)
    if key not in defs.hooks:
        defs.hooks[key] = _hook_base_uncached(name, bases, defs, lines, depth)
    return defs.hooks[key]


def _hook_base_uncached(
    name: str, bases: list[str], defs: _Definitions, lines: _SourceLines, depth: int
) -> str | None:
    for base in bases:
        if _is_non_hook_base(base):
            continue
        project = defs.classes_named(base)
        if not project:
            return base
        for nid in project:
            n = defs.byid[nid]
            file, line = _node_file(n), _int_line(n)
            if name in defs.member_names(nid):
                return base
            span = _span_for(file, line, lines)
            if span is not None and name in lines.tokens(file, span):
                return base
            text = lines.get(file)
            if depth < 4 and 0 < line <= len(text):
                above = _hook_base(name, _class_bases(text[line - 1]), defs, lines, depth + 1)
                if above:
                    return above
    return None


def _span_for(
    file: str, line: int, lines: _SourceLines | None = None
) -> tuple[int, int] | None:
    """(region_start, end) of the definition at ``line``, from the span engine.

    With ``lines``, the file's spans come from that per-call index.
    """
    if line <= 0:
        return None
    if lines is not None:
        exact = lines.spans_at(file, line)
        spans = lines.spans(file) if not exact else []
    else:
        spans = _spans_for_file(file)
        exact = [s for s in spans if s[2] == line]
    here = exact or [s for s in spans if s[0] <= line <= s[1]]
    if not here:
        return None
    rs, end, _dl, _q = min(here, key=lambda s: s[1] - s[0])
    return rs, end


class _SourceLines:
    """Per-call cache of file lines and spans, read through the project confinement.

    An analysis asks for the same file's spans once per candidate it holds, and the
    same class body once per subclass method: on a graph of Django's size that is
    hundreds of thousands of asks. Each file's spans are read once here (indexed by
    definition line), and each span's identifiers tokenized once.
    """

    def __init__(self) -> None:
        self._files: dict[str, list[str]] = {}
        self._spans: dict[str, tuple[
            list[tuple[int, int, int, str]], dict[int, list[tuple[int, int, int, str]]]
        ]] = {}
        self._tokens: dict[tuple[str, int, int], frozenset[str]] = {}
        self._children: dict[str, dict[str, list[str]]] = {}

    def spans(self, file: str) -> list[tuple[int, int, int, str]]:
        return self._indexed(file)[0]

    def spans_at(self, file: str, line: int) -> list[tuple[int, int, int, str]]:
        """The spans whose definition line is ``line``, in the file's span order."""
        return self._indexed(file)[1].get(line, [])

    def _indexed(self, file: str) -> tuple[
        list[tuple[int, int, int, str]], dict[int, list[tuple[int, int, int, str]]]
    ]:
        got = self._spans.get(file)
        if got is None:
            spans = _spans_for_file(file)
            by_line: dict[int, list[tuple[int, int, int, str]]] = {}
            for s in spans:
                by_line.setdefault(s[2], []).append(s)
            got = (spans, by_line)
            self._spans[file] = got
        return got

    def children(self, file: str) -> dict[str, list[str]]:
        """Qualified name -> the last names of the spans directly under it."""
        got = self._children.get(file)
        if got is None:
            got = {}
            for _rs, _e, _dl, q in self.spans(file):
                if "." in q:
                    parent, last = q.rsplit(".", 1)
                    got.setdefault(parent, []).append(last)
            self._children[file] = got
        return got

    def tokens(self, file: str, span: tuple[int, int]) -> frozenset[str]:
        """The identifiers written in lines ``span[0]..span[1]`` of ``file``."""
        key = (file, span[0], span[1])
        got = self._tokens.get(key)
        if got is None:
            got = frozenset(_IDENT.findall("\n".join(self.get(file)[span[0] - 1:span[1]])))
            self._tokens[key] = got
        return got

    def get(self, file: str) -> list[str]:
        got = self._files.get(file)
        if got is None:
            got = []
            full = _resolve_in_project(file)
            if full is not None:
                try:
                    got = full.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    got = []
            self._files[file] = got
        return got


def _decorators(
    n: dict, nid: str, edges_out: dict[str, list[tuple[str, dict]]], byid: dict[str, dict],
    lines: _SourceLines,
) -> list[str]:
    """Decorator / annotation names on a definition.

    From the graph where it recorded them (a ``references`` edge whose context is
    ``decorator``), and from the source lines above the definition, which works in
    any language the span engine reads.
    """
    found: list[str] = []
    for target, e in edges_out.get(nid, ()):
        if str(e.get("context") or "").lower() == "decorator":
            t = byid.get(target)
            found.append(_node_label(t) if t else target)
    file, line = _node_file(n), _int_line(n)
    span = _span_for(file, line, lines)
    if span is not None:
        text_lines = lines.get(file)
        # Above the definition (Python, JS, Rust: decorators are siblings) and from
        # its first line down to the one that names it (Java `@Test` / C# `[Fact]`
        # start the declaration itself).
        name = _bare_name(_node_label(n))
        below = text_lines[line - 1:line - 1 + _DECORATOR_REACH]
        stop = next((i for i, text in enumerate(below) if name and name in text), 0)
        in_comment = False
        for text in text_lines[span[0] - 1:line - 1] + below[:stop + 1]:
            # a doc comment's `@param` / `@default` tag is no decorator, with or
            # without the leading `*`. Only a line that starts a comment opens
            # one: `@Test("matches */*")` holds the characters in a string.
            stripped = text.lstrip()
            if in_comment:
                in_comment = "*/" not in text
                continue
            if stripped.startswith("/*"):
                in_comment = "*/" not in stripped[2:]
                continue
            m = _DECORATOR_LINE.match(text)
            if m:
                found.append(m.group(1))
    seen: set[str] = set()
    out: list[str] = []
    for d in found:
        short = d.replace("::", ".").rsplit(".", 1)[-1].rstrip("()")
        if short and short.lower() not in seen:
            seen.add(short.lower())
            out.append(short)
    return out


def _count_mentions(
    names: set[str],
) -> tuple[dict[str, Counter[str]], dict[str, Counter[str]], int, bool]:
    """How often each name occurs as a whole identifier, per text file of the project.

    Walks ``config.PROJECT_DIR`` once: templates, configs and docs count too, since
    a function named in ``pyproject.toml`` or called from an HTML attribute is not
    dead. Hidden directories, dependency and build trees, vendored or oversized
    files and binaries are skipped. Returns ({name: {file: count}}, the same for
    attribute accesses only (``obj.name``), files read, hit the file cap) — per
    file and per form, so that a second definition of the same name and its bare
    uses can be told apart from uses of the candidate.
    """
    counts: dict[str, Counter[str]] = {}
    attrs: dict[str, Counter[str]] = {}
    if not names:
        return counts, attrs, 0, False
    root = config.PROJECT_DIR.resolve()
    scanned = 0
    capped = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in _MENTION_SKIP_DIRS
            and d != config.OUT_DIR_NAME
        ]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if _vendored_by_path(rel) or os.path.islink(full):
                continue  # a link may point outside the project: never follow it
            try:
                if os.path.getsize(full) > _MENTION_MAX_BYTES:
                    continue
                with open(full, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            if b"\0" in raw[:2048]:
                continue  # binary
            if scanned >= _MENTION_MAX_FILES:
                capped = True
                break
            scanned += 1
            for m in _MENTION.finditer(raw.decode("utf-8", "replace")):
                tok = m.group(2)
                if tok in names:
                    counts.setdefault(tok, Counter())[rel] += 1
                    if m.group(1):
                        attrs.setdefault(tok, Counter())[rel] += 1
        if capped:
            break
    return counts, attrs, scanned, capped


def _is_test_definition(name: str, file: str) -> bool:
    """A test the runner collects by naming convention, never by reference:
    ``test_x`` / ``TestX``, googletest's ``TEST`` / ``TEST_F``, Go's ``BenchmarkX`` /
    ``ExampleX`` / ``FuzzX``, the ``XTest`` / ``XTests`` / ``XTestCase`` classes of
    the JVM, .NET, PHP and Swift, and the ``setUp`` / ``tearDown`` fixtures."""
    return _is_test_path(file) and (
        name.startswith(("test", "Test", "TEST", "Benchmark", "Example", "Fuzz"))
        or name.endswith(("Test", "Tests", "TestCase"))
        # xUnit fixtures: `setUp`, `tearDown`, `setUpBeforeClass`
        or name.lower().startswith(("setup", "teardown"))
    )


_TYPE_ARGUMENTS = re.compile(r"<[^<>]*>")


def _never_called_by_name(name: str, label: str, parent_label: str) -> bool:
    """A definition no caller writes the name of, so "nothing refers to it" says
    nothing: an anonymous function (``{closure#3}``, ``<lambda>``), an operator
    (C++ ``operator[]``, Ruby ``[]=``), a constructor or destructor — used
    whenever its class is."""
    if not name or name[0] in "{<(~" or not any(ch.isalnum() or ch == "_" for ch in name):
        return True
    if name.startswith("operator") and not (name[8:9].isalnum() or name[8:9] == "_"):
        return True
    plain = _TYPE_ARGUMENTS.sub("", name)
    if parent_label and _TYPE_ARGUMENTS.sub("", _bare_name(parent_label)) == plain:
        return True  # `.Foo()` under class Foo
    scope = _TYPE_ARGUMENTS.sub("", label.strip().removesuffix("()")).replace("::", ".")
    parts = scope.lstrip(".").split(".")
    return len(parts) > 1 and parts[-2] == parts[-1]  # `Foo::Foo()`


def _is_anonymous_class(n: dict, lines: _SourceLines) -> bool:
    """A class graphify made of an anonymous class body, named after the type it
    implements: Java ``new TypeToken<List<String>>() { … }``, Kotlin
    ``object : Callback<T> { … }``. Nothing can refer to it by that name."""
    text = lines.get(_node_file(n))
    line = _int_line(n)
    if not 0 < line <= len(text):
        return False
    src = text[line - 1]
    label = _node_label(n).split("<", 1)[0]
    return bool(label) and (f"new {label}" in src or bool(
        re.search(rf"\bobject\s*:\s*{re.escape(label)}\b", src)))


def _unreferenced(
    scoped_nodes: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
    count_test_references: bool = True,
) -> dict[str, Any]:
    """Definitions in ``scoped_nodes`` that nothing in the graph or the text refers to.

    References are counted over the WHOLE graph, whatever the scope: the scope
    picks which definitions to examine, not who may refer to them.
    """
    defs = _Definitions(all_nodes, all_edges)
    edges_out: dict[str, list[tuple[str, dict]]] = {}
    for e in all_edges:
        s, t = _edge_ends(e)
        edges_out.setdefault(s, []).append((t, e))

    examined = 0
    no_graph_ref: list[tuple[str, dict, str, str]] = []  # (id, node, kind, bare name)
    lines = _SourceLines()
    for n in scoped_nodes:
        nid = _node_id(n)
        kind = defs.kind.get(nid)
        if not kind:
            continue
        # Ruby `success?` / `save!` / `body=` are written `success?`, `body = x`:
        # the identifier a caller types is the name without the suffix
        name = _bare_name(_node_label(n)).rstrip("?!=")
        file = _node_file(n)
        if not name or (name.startswith("__") and (
                name.endswith("__") or file.lower().endswith(".php"))):
            # Python dunders and PHP magic methods (`__invoke`, `__get`) are
            # called by the language, not by name
            continue
        if _is_test_definition(name, file):
            continue
        owner = defs.byid.get(defs.parent.get(nid, ""))
        if _never_called_by_name(name, _node_label(n), _node_label(owner) if owner else ""):
            continue
        if kind == "class" and _is_anonymous_class(n, lines):
            continue  # `new Callback<T>() { … }` / `object : Callback<T> { … }`
        examined += 1
        family = {nid, *defs.members.get(nid, ())}
        referenced = False
        for target in family:
            for src, _e in defs.incoming.get(target, ()):
                if src in family:
                    continue
                if not count_test_references:
                    sn = defs.byid.get(src)
                    if sn is not None and _is_test_node(sn):
                        continue
                referenced = True
                break
            if referenced:
                break
        if not referenced:
            no_graph_ref.append((nid, n, kind, name))

    registered: Counter[str] = Counter()
    remaining: list[tuple[str, dict, str, str]] = []
    for nid, n, kind, name in no_graph_ref:
        decos = [
            d for d in _decorators(n, nid, edges_out, defs.byid, lines)
            if d.lower() not in _NEUTRAL_DECORATORS
        ]
        if decos:
            registered[decos[0]] += 1
        else:
            remaining.append((nid, n, kind, name))

    names = {name for _nid, _n, _kind, name in remaining}
    # A class can be used only through its members: a C# extension class's
    # methods are called on the extended type (`client.BuildUri(...)`) and the
    # class is never named. Its members come from the span engine, since the
    # graph may not see them (C# 14 `extension(...)` blocks).
    graph_names = {_bare_name(_node_label(defs.byid[d])) for d in defs.kind}
    members_of: dict[str, list[str]] = {}
    for nid, n, kind, _name in remaining:
        if kind != "class":
            continue
        cls_span = _span_of_definition(_node_file(n), _int_line(n), _name)
        if cls_span is None:
            continue
        cls_qual = cls_span[3]
        members_of[nid] = sorted({
            m for m in lines.children(_node_file(n)).get(cls_qual, ())
            if len(m) >= 4 and m not in names
        })
    member_names = {m for ms in members_of.values() for m in ms}
    per_file, per_file_attrs, files_scanned, capped = _count_mentions(names | member_names)
    graph_bases: dict[str, list[str]] = {}  # class id -> base names the graph recorded
    for e in all_edges:
        if _rel(e) in _INHERITANCE_RELATIONS:
            src, dst = _edge_ends(e)
            base = defs.byid.get(dst)
            graph_bases.setdefault(src, []).append(
                _bare_name(_node_label(base)) if base else dst
            )
    # Where each candidate name is defined (any kind). In a file that has its OWN
    # symbol of that name, the `def` line and every bare use (`name(...)`,
    # `x: name`) belong to that symbol: a candidate has no incoming import edge,
    # so the file cannot be using this one by its bare name. An attribute access
    # (`obj.name`, `mod.name`) can never mean the file's own module-level symbol,
    # and is how a method is called: it still counts.
    defined_in: dict[str, dict[str, list[int]]] = {}
    for other in defs.kind:
        on = defs.byid[other]
        bare = _bare_name(_node_label(on)).rstrip("?!=")
        if bare in names:
            defined_in.setdefault(bare, {}).setdefault(_node_file(on), []).append(
                _int_line(on)
            )
    items: list[dict[str, Any]] = []
    homes_sorted: dict[str, list[tuple[str, str, int]]] = {}
    has_referencing_files: dict[str, bool] = {}
    for nid, n, kind, name in remaining:
        file, line = _node_file(n), _int_line(n)
        own = 1
        span = _span_for(file, line, lines)
        if span is not None:
            body = "\n".join(lines.get(file)[span[0] - 1:span[1]])
            own = max(1, sum(1 for tok in _IDENT.findall(body) if tok == name))
        homes = defined_in.get(name, {})
        mentions = 0
        for where, count in per_file.get(name, {}).items():
            if where == file:
                # its own body, and the `def` line of any namesake next to it
                count -= own + max(0, len(homes.get(file, ())) - 1)
            elif where in homes:
                attrs_here = per_file_attrs.get(name)
                count = attrs_here[where] if attrs_here else 0
            mentions += max(0, count)
        via = [
            m for m in members_of.get(nid, ())
            if m not in graph_names  # a name the graph knows can mean that other symbol
            and any(f != file and c for f, c in (per_file_attrs.get(m) or {}).items())
        ]
        if via and not mentions:
            mentions = sum(c for m in via for f, c in per_file_attrs[m].items() if f != file)
        item: dict[str, Any] = {
            "id": nid, "name": name, "kind": kind, "file": file, "line": line or None,
            "mentions": mentions,
        }
        if via and mentions:
            item["used_through_members"] = via[:5]
        # sorted once per name: a name defined in hundreds of files (Django's
        # `get_queryset`) is a candidate in many of them
        everywhere = homes_sorted.get(name)
        if everywhere is None:
            everywhere = homes_sorted[name] = sorted(
                (f"{f}:{ln}", f, ln) for f, lns in homes.items() for ln in lns
            )
        namesakes = []
        for where_at, f, ln in everywhere:
            if (f, ln) != (file, line):
                namesakes.append(where_at)
                if len(namesakes) == 5:
                    break
        if namesakes:
            item["also_defined"] = namesakes
        parent = defs.parent.get(nid, "")
        bases = list(graph_bases.get(parent, ()))
        bases += _enclosing_class_bases(file, line, lines) or []
        hook = _hook_base(name, bases, defs, lines) if bases else None
        if hook:
            # called through the base class, which the graph cannot follow
            item["note"] = (
                f"method of a subclass of {hook}: may override a hook its base class calls"
            )
        elif kind == "method" and not name.startswith("_") and parent in defs.byid:
            used = has_referencing_files.get(parent)
            if used is None:
                used = has_referencing_files[parent] = bool(defs.referencing_files(parent))
            if used:
                item["note"] = "public method of a class used elsewhere: unused here, may be API"
        items.append(item)
    items.sort(key=lambda it: (it["mentions"], "note" in it, it["file"], it["line"] or 0))
    return {
        "examined": examined,
        "no_graph_reference": len(no_graph_ref),
        "registered_by_decorator": dict(registered.most_common()),
        "items": items,
        "files_scanned_for_mentions": files_scanned,
        "mention_scan_capped": capped,
    }


# ---------------------------------------------------------------------------
# Same-named symbols
# ---------------------------------------------------------------------------

# Names every script or module is expected to repeat.
_CONVENTIONAL_NAMES = frozenset({"main", "setup", "teardown"})


def _is_nested_definition(n: dict) -> bool:
    """Written inside another function or class, by the source's own nesting.

    The graph's containment is not enough: graphify sometimes hangs a closure
    directly on its file. The span engine's qualified name does not lie.
    """
    line = _int_line(n)
    span = next((sp for sp in _spans_for_file(_node_file(n)) if sp[2] == line), None)
    return span is not None and "." in span[3]


def _used_in_own_file(nid: str, n: dict, defs: _Definitions, lines: _SourceLines) -> bool:
    """Does the definition's own file refer to it, by a graph edge or by name?"""
    file, line = _node_file(n), _int_line(n)
    family = {nid, *defs.members.get(nid, ())}
    for target in family:
        for src, _e in defs.incoming.get(target, ()):
            sn = defs.byid.get(src)
            if src not in family and sn is not None and _node_file(sn) == file:
                return True
    name = _bare_name(_node_label(n))
    text = lines.get(file)
    span = _span_for(file, line, lines)
    if not text or span is None:
        return False
    outside = text[:span[0] - 1] + text[span[1]:]
    return any(tok == name for tok in _IDENT.findall("\n".join(outside)))


def _same_named(
    scoped_nodes: list[dict], all_nodes: list[dict], all_edges: list[dict],
    kinds: frozenset[str] = frozenset({"class", "function"}),
    across_languages: bool = False,
) -> list[dict[str, Any]]:
    """Top-level classes / functions that share a name across different files.

    Names are compared within one language family unless ``across_languages``: a
    Python function and a JavaScript function of the same name are not copies of
    each other.
    """
    defs = _Definitions(all_nodes, all_edges)
    lines = _SourceLines()
    groups: dict[tuple[str, str], list[tuple[str, dict, str]]] = {}
    for n in scoped_nodes:
        nid = _node_id(n)
        kind = defs.kind.get(nid)
        if kind not in kinds or not defs.is_top_level(nid) or _is_nested_definition(n):
            continue
        name = _bare_name(_node_label(n))
        if not name or name in _CONVENTIONAL_NAMES or (
            name.startswith("__") and name.endswith("__")
        ):
            continue
        family = "" if across_languages else (_lang_family(_node_file(n)) or "")
        groups.setdefault((name, family), []).append((nid, n, kind))
    out: list[dict[str, Any]] = []
    for (name, family), members in groups.items():
        if len({_node_file(n) for _nid, n, _k in members}) < 2:
            continue
        definitions: list[dict[str, Any]] = []
        for nid, n, kind in members:
            definitions.append({
                "id": nid, "kind": kind, "file": _node_file(n), "line": _int_line(n) or None,
                "imported_by": len(defs.referencing_files(nid, _IMPORT_RELATIONS)),
                "referenced_by": len(defs.referencing_files(nid)),
                "used_in_own_file": _used_in_own_file(nid, n, defs, lines),
            })
        definitions.sort(key=lambda d: (-d["imported_by"], -d["referenced_by"], d["file"]))
        out.append({
            "name": name,
            "language": family or None,
            "kinds": sorted({d["kind"] for d in definitions}),
            "modules": len({d["file"] for d in definitions}),
            "definitions": definitions,
        })
    out.sort(key=lambda g: (
        "class" not in g["kinds"],
        -sum(d["imported_by"] for d in g["definitions"]),
        -g["modules"],
        g["name"],
    ))
    return out


# ---------------------------------------------------------------------------
# Identical bodies
# ---------------------------------------------------------------------------

# A body smaller than this says nothing when it repeats: `return self.x`, `pass`,
# `raise NotImplementedError`. Counted in AST nodes (Python, operators and load /
# store contexts aside) or leaf tokens (tree-sitter, punctuation included).
_BODY_MIN_NODES = 8
_BODY_MIN_TOKENS = 14
_AST_NOISE = (ast.expr_context, ast.operator, ast.boolop, ast.unaryop, ast.cmpop)
_TS_FUNCTION_HINTS = ("function", "method", "arrow", "constructor", "func", "lambda")

# rel path -> (mtime, {def line: (digest, body lines)}); the span cache's scheme.
_BODY_CACHE: dict[str, tuple[float, Any]] = {}


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()


def _python_bodies(src: bytes) -> dict[int, tuple[str, int]]:
    try:
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001 - unparseable file: no fingerprints
        return {}
    out: dict[int, tuple[str, int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = list(node.body)
        if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]  # the docstring is not the body
        size = sum(
            1 for stmt in body for x in ast.walk(stmt) if not isinstance(x, _AST_NOISE)
        )
        if size < _BODY_MIN_NODES:
            continue
        # ast.dump carries no positions or comments, so two copies that differ
        # only in layout, comments or docstring dump identically.
        dumped = "\n".join(ast.dump(stmt) for stmt in body)
        end = getattr(node, "end_lineno", None) or node.lineno
        out[node.lineno] = (_digest(dumped), end - body[0].lineno + 1)
    return out


def _treesitter_bodies(src: bytes, rel: str) -> dict[int, tuple[str, int]]:
    parser, _lang = _ts_parser_for(rel, src)
    if parser is None:
        return {}
    try:
        root = parser.parse(src).root_node
    except Exception:  # noqa: BLE001
        return {}
    out: dict[int, tuple[str, int]] = {}

    def record(fn: Any) -> None:
        body = fn.child_by_field_name("body")
        if body is None:
            return
        skeleton = _ts_skeleton(body)  # comments dropped, whitespace is no token
        if skeleton is None or sum(1 for part in skeleton if len(part) == 2) < _BODY_MIN_TOKENS:
            return
        out[fn.start_point[0] + 1] = (
            _digest(repr(skeleton)), body.end_point[0] - body.start_point[0] + 1
        )

    def walk(node: Any) -> None:
        for child in node.named_children:
            bound = _ts_bound_function(child)
            if bound is not None:
                record(bound[1])
            elif _is_ts_symbol(child.type) and any(
                h in child.type.lower() for h in _TS_FUNCTION_HINTS
            ):
                record(child)
            walk(child)

    try:
        walk(root)
    except RecursionError:
        pass
    return out


def _body_fingerprints(file: str) -> dict[int, tuple[str, int]]:
    """{def line: (digest of the normalized body, body line count)} for one file.

    Normalized means: whitespace, comments and (Python) the docstring do not
    count, and neither does the function's own name or signature — a renamed copy
    still matches. Python via ``ast``, other languages via tree-sitter when
    present. Bodies too small to mean anything are left out.
    """
    def compute(src: bytes, rel: str) -> dict[int, tuple[str, int]]:
        if rel.lower().endswith((".py", ".pyi")):
            return _python_bodies(src)
        return _treesitter_bodies(src, rel)

    return _cached_file_analysis(_BODY_CACHE, file, compute, dict)


def _identical_bodies(
    scoped_nodes: list[dict], all_nodes: list[dict], all_edges: list[dict],
    include_tests: bool = False,
) -> list[dict[str, Any]]:
    """Functions and methods whose normalized bodies are identical, as groups.

    Exact and model-free, and it covers every size the semantic tier's
    largest-first seeding never reaches: small helpers are what gets copied.
    """
    defs = _Definitions(all_nodes, all_edges)
    groups: dict[str, list[dict[str, Any]]] = {}
    for n in scoped_nodes:
        nid = _node_id(n)
        if defs.kind.get(nid) not in ("function", "method"):
            continue
        file, line = _node_file(n), _int_line(n)
        if not include_tests and _is_test_path(file):
            continue
        fp = _body_fingerprints(file).get(line)
        if fp is None:
            continue
        groups.setdefault(fp[0], []).append(
            {"id": nid, "file": file, "line": line, "lines": fp[1]}
        )
    out: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda d: (d["file"], d["line"]))
        out.append({
            "copies": len(members),
            "lines": max(d["lines"] for d in members),
            "definitions": [{"id": d["id"], "file": d["file"], "line": d["line"]}
                            for d in members],
        })
    out.sort(key=lambda g: (-g["copies"] * g["lines"], g["definitions"][0]["file"]))
    return out


# ---------------------------------------------------------------------------
# Layer rules
# ---------------------------------------------------------------------------


def _normalize_rules(raw: object) -> dict[str, list[str]] | str:
    """Validate ``{layer prefix: [prefixes it may import]}``; str = error message."""
    if isinstance(raw, dict) and isinstance(raw.get("layers"), dict):
        raw = raw["layers"]  # the file form may wrap the mapping
    if not isinstance(raw, dict) or not raw:
        return (
            "ERROR: layer rules must be a non-empty object mapping a path prefix to the "
            'prefixes it may import, e.g. {"src/app/api": ["src/app/core"], '
            '"src/app/core": []}.'
        )
    rules: dict[str, list[str]] = {}
    for key, allowed in raw.items():
        layer = _clean_pattern(str(key))
        if not layer:
            return f"ERROR: empty layer prefix in the rules ({key!r})."
        if isinstance(allowed, str):
            allowed = [allowed]
        if not isinstance(allowed, list | tuple):
            return f"ERROR: the rule for '{key}' must be a list of path prefixes."
        rules[layer] = [
            a for a in ("*" if str(x).strip() == "*" else _clean_pattern(str(x)) for x in allowed)
            if a
        ]
    return rules


def _layer_of(path: str, rules: dict[str, list[str]]) -> str | None:
    """The most specific declared layer holding ``path`` (longest matching prefix)."""
    best: str | None = None
    for layer in rules:
        if _path_matches(layer, path) and (best is None or len(layer) > len(best)):
            best = layer
    return best


def _layer_violations(
    nodes: list[dict], edges: list[dict], rules: dict[str, list[str]], strict: bool = False
) -> dict[str, Any]:
    """Import edges that break ``rules``, grouped by the importing layer.

    An import is allowed inside one layer, into any prefix the layer lists, and —
    unless ``strict`` — into files that belong to no declared layer. Imports of
    external packages are never checked. Each edge is first checked against the
    importing file's own statements: one the source contradicts (``import yaml``
    linked to a first-party ``yaml.py``) is dropped and listed under
    ``rejected_imports``, and a violation says when its import runs.
    """
    checker = ImportChecker()
    files = sorted({_norm_relpath(_node_file(n)) for n in nodes} - {""})
    layers: dict[str, dict[str, Any]] = {
        layer: {"layer": layer, "may_import": allowed, "files": 0,
                "imports_checked": 0, "violations": []}
        for layer, allowed in rules.items()
    }
    for f in files:
        layer = _layer_of(f, rules)
        if layer is not None:
            layers[layer]["files"] += 1
    seen: set[tuple[str, str, str]] = set()
    for sf, tf, e, dst, kind in _file_import_edges(nodes, edges, checker.kind):
        src_layer = _layer_of(sf, rules)
        if src_layer is None:
            continue
        entry = layers[src_layer]
        loc = str(e.get("source_location") or "")
        key = (sf, tf, loc)
        if key in seen:
            continue
        seen.add(key)
        entry["imports_checked"] += 1
        dst_layer = _layer_of(tf, rules)
        if dst_layer == src_layer:
            continue
        allowed = rules[src_layer]
        if "*" in allowed or any(_path_matches(a, tf) for a in allowed):
            continue
        if dst_layer is None and not strict:
            continue
        violation: dict[str, Any] = {
            "from_file": sf, "to_file": tf, "to_layer": dst_layer,
            "site": f"{sf}:{loc}" if loc else sf, "import": kind,
        }
        if not _is_file_hub_node(dst):
            violation["imported"] = _node_label(dst)
        entry["violations"].append(violation)
    for entry in layers.values():
        entry["violations"].sort(key=lambda v: (v["from_file"], v["site"], v["to_file"]))
    unmatched = sorted(
        {layer for layer, entry in layers.items() if entry["files"] == 0}
        | {a for allowed in rules.values() for a in allowed
           if a != "*" and not any(_path_matches(a, f) for f in files)}
    )
    return {
        "layers": list(layers.values()),
        "violation_count": sum(len(entry["violations"]) for entry in layers.values()),
        "imports_checked": sum(entry["imports_checked"] for entry in layers.values()),
        "unmatched_prefixes": unmatched,
        "rejected_imports": checker.rejected,
    }
