"""The optional dense tier of the built-in search: static embeddings.

A model2vec static model (``minishlab/potion-code-16M-v2`` by default, the one
semble 0.7 uses, so the Hugging Face cache is shared) turns each chunk into a
vector; similarity is a dot product of L2-normalised rows. Everything here is
optional: ``model2vec`` and ``numpy`` come from the ``[embed]`` extra and are
imported inside functions, and every failure — the extra missing, the model not
downloadable, ``GRAPHLORE_OFFLINE`` with no cached copy — is a ``(None, reason)``
the caller reports, never an exception.

Vectors are keyed by a hash of the model id and the embedded text, so editing a
file re-embeds only the definitions whose text changed. They can be kept on disk
under the user cache directory (``GRAPHLORE_CACHE_DIR``), never in the project.

Depends only on :mod:`graphlore.config`.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

from . import config

DEFAULT_MODEL = "minishlab/potion-code-16M-v2"
# Characters of a chunk that are embedded. A static model averages token vectors,
# so the head of a long body carries its meaning; the rest only costs time.
_EMBED_MAX_CHARS = 4000
# Above this many chunks the dense tier is off for the project (about 100 MB of
# float32 vectors at 256 dimensions) and the reply says so.
EMBED_MAX_CHUNKS = 100_000
_OFF = frozenset({"off", "none", "0", "false", "no"})

# spec -> (model or None, reason it is None). Failures are cached too: a model that
# cannot be downloaded would otherwise cost a network timeout on every call.
_MODEL_CACHE: dict[tuple[str, bool], tuple[Any, str]] = {}
_MODEL_LOCK = threading.Lock()


def model_spec() -> str:
    """``GRAPHLORE_EMBED_MODEL``: a Hugging Face repo id, a local directory, or
    ``off``. Default: :data:`DEFAULT_MODEL`."""
    return (config.env("EMBED_MODEL", "") or "").strip() or DEFAULT_MODEL


def dense_wanted() -> bool:
    return model_spec().lower() not in _OFF


# How to get the model onto a machine before it is needed; quoted in replies that
# ran without the dense tier because the model could not be loaded.
MODEL_PREFETCH_NOTE = (
    "To fetch it ahead of time: python -c \"from model2vec import StaticModel; "
    f"StaticModel.from_pretrained('{DEFAULT_MODEL}')\" — then copy "
    "~/.cache/huggingface to a machine without network access, or set "
    "GRAPHLORE_EMBED_MODEL to a local copy of the model."
)


def _load_model(spec: str) -> tuple[Any, str]:
    """Load the static model, or ``(None, why not)``. Cached per (spec, offline)."""
    key = (spec, config.offline())
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached
        result = _load_model_uncached(spec)
        _MODEL_CACHE[key] = result
        return result


def _load_model_uncached(spec: str) -> tuple[Any, str]:
    config.export_offline_env()  # before the import: huggingface_hub reads it then
    try:
        import numpy  # noqa: F401
        from model2vec import StaticModel
    except ImportError:
        return None, (
            "the dense tier needs the optional [embed] extra (model2vec, numpy): "
            "pip install 'graphlore[embed]'"
        )
    try:
        try:
            model = StaticModel.from_pretrained(spec, force_download=False)
        except TypeError:  # a model2vec without the keyword
            model = StaticModel.from_pretrained(spec)
    except Exception as exc:  # noqa: BLE001 - any load failure degrades to BM25 only
        why = f"{type(exc).__name__}: {str(exc).strip()[:200]}"
        if config.offline():
            return None, (
                f"embedding model {spec} is not in the local Hugging Face cache and "
                f"GRAPHLORE_OFFLINE is set ({why}). " + MODEL_PREFETCH_NOTE
            )
        return None, f"embedding model {spec} could not be loaded ({why}). " + MODEL_PREFETCH_NOTE
    return model, ""


def text_key(spec: str, text: str) -> bytes:
    """The cache key of one embedded text under one model."""
    h = hashlib.blake2b(digest_size=16)
    h.update(spec.encode("utf-8", "replace"))
    h.update(b"\0")
    h.update(text[:_EMBED_MAX_CHARS].encode("utf-8", "replace"))
    return h.digest()


def _encode(model: Any, texts: list[str]) -> Any:
    """Rows of L2-normalised float32 vectors, one per text."""
    import numpy as np

    vecs = np.asarray(
        model.encode([t[:_EMBED_MAX_CHARS] for t in texts]), dtype=np.float32
    )
    if vecs.ndim != 2:
        vecs = vecs.reshape(len(texts), -1)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def _cache_root() -> Path | None:
    """Where vectors are kept between runs, or None when disabled
    (``GRAPHLORE_CACHE_DIR=off``)."""
    return config.cache_root()


class VectorStore:
    """Vectors of chunk texts for one project and one model, by text hash.

    ``vectors_for(keys, texts)`` embeds the texts it has not seen and returns a
    matrix in key order. ``save(live)`` keeps the vectors of the live keys on disk
    (atomic replace; any failure is ignored: the cache only saves time).
    """

    def __init__(self, model: Any, spec: str, project: str) -> None:
        self.model = model
        self.spec = spec
        self.dim = 0
        self._vecs: dict[bytes, Any] = {}
        self._dirty = False
        self._path: Path | None = None
        root = _cache_root()
        if root is not None:
            pid = hashlib.blake2b(project.encode("utf-8", "replace"), digest_size=8).hexdigest()
            mid = hashlib.blake2b(spec.encode("utf-8", "replace"), digest_size=6).hexdigest()
            self._path = root / pid / f"vectors-{mid}.npz"
            self._load()

    def _load(self) -> None:
        import numpy as np

        if self._path is None or not self._path.is_file():
            return
        try:
            with np.load(self._path, allow_pickle=False) as data:
                keys = data["keys"]
                mat = data["vectors"].astype(np.float32)
        except Exception:  # noqa: BLE001 - a corrupt cache is a cold cache
            return
        if keys.ndim != 1 or mat.ndim != 2 or len(keys) != len(mat):
            return
        self.dim = int(mat.shape[1])
        for k, row in zip(keys.tolist(), mat, strict=False):
            self._vecs[bytes(k)] = row

    def __len__(self) -> int:
        return len(self._vecs)

    def vectors_for(self, keys: list[bytes], texts: list[str]) -> tuple[Any, int]:
        """(matrix in key order, how many texts were newly embedded)."""
        import numpy as np

        missing = [(k, t) for k, t in zip(keys, texts, strict=True) if k not in self._vecs]
        if missing:
            new = _encode(self.model, [t for _k, t in missing])
            self.dim = int(new.shape[1])
            for (k, _t), row in zip(missing, new, strict=True):
                self._vecs[k] = row
            self._dirty = True
        if not keys:
            return np.zeros((0, self.dim or 1), dtype=np.float32), 0
        return np.stack([self._vecs[k] for k in keys]), len(missing)

    def query(self, text: str) -> Any:
        return _encode(self.model, [text])[0]

    def save(self, live: set[bytes]) -> None:
        """Write the live vectors to disk and forget the dead ones."""
        import numpy as np

        dead = [k for k in self._vecs if k not in live]
        for k in dead:
            del self._vecs[k]
        if not (self._dirty or dead) or self._path is None or not self._vecs:
            self._dirty = False
            return
        self._dirty = False
        keys = list(self._vecs)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(f".tmp{os.getpid()}.npz")
            np.savez(
                tmp,
                keys=np.array(keys, dtype="S16"),
                vectors=np.stack([self._vecs[k] for k in keys]).astype(np.float16),
            )
            os.replace(tmp, self._path)
        except Exception:  # noqa: BLE001 - the cache only saves time
            return


def top_k(matrix: Any, q: Any, k: int, alive: Any = None) -> list[tuple[int, float]]:
    """(row, cosine) of the k rows of ``matrix`` closest to ``q``, best first,
    ties by row. ``alive`` masks rows out (False = skip)."""
    import numpy as np

    if matrix is None or len(matrix) == 0 or k <= 0:
        return []
    sims = matrix @ q
    if alive is not None:
        sims = np.where(alive, sims, -np.inf)
    k = min(k, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    rows = sorted(((int(i), float(sims[i])) for i in idx), key=lambda r: (-r[1], r[0]))
    return [r for r in rows if r[1] != float("-inf")]
