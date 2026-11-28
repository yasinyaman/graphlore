SCHEMA_VERSION: int
__version__: str

def build(
    root: str,
    out: str | None = None,
    commit: str | None = None,
    documents: bool = True,
    out_dir_name: str = "graphlore-out",
    max_file_bytes: int = ...,
    parse_timeout_ms: int = 10000,
    threads: int | None = None,
    incremental: bool = False,
    unresolved: str | None = None,
) -> str: ...
def cluster(graph: str) -> str: ...
def file_spans(src: bytes, rel: str) -> list[tuple[int, int, int, str]]: ...
def file_facts(src: bytes, rel: str) -> str: ...
def languages() -> list[tuple[str, list[str]]]: ...
