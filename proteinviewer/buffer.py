"""The structures the viewer is working on, held on disk.

Every tool behind the viewer is a command line program: it reads a file and
writes its results into a directory beside it. So whatever the browser is
displaying is posted once, written here, and every later run reads it from
this location rather than the coordinates being posted again per request.

Each entry gets its own directory so tool output cannot collide between
structures. Thread-safe: the server handles requests on many threads.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from .errors import BadRequest, NotFound
from .structure import Structure, detect_format

__all__ = ["StructureBuffer", "BufferedStructure", "PocketRun", "safe_token"]


@dataclass(frozen=True)
class PocketRun:
    """The last pocket run on a structure. ``key`` is everything that makes
    two runs the same run; a request with the same key is a cache hit."""
    key: tuple
    pockets: list[dict]
    meta: dict


@dataclass(eq=False)
class BufferedStructure:
    token: str
    fmt: str            # "pdb" or "cif"
    path: str           # the posted text, as written
    workdir: str        # scratch space for every tool run on it
    digest: str         # of the posted text: same digest, same structure
    structure: Structure
    pockets: PocketRun | None = None
    _locks: dict[str, threading.Lock] = field(default_factory=dict, repr=False)
    _guard: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def lock(self, purpose: str) -> threading.Lock:
        """One lock per kind of work on this structure. Two pocket runs share
        an output directory, so they take turns; a pocket run and a
        preparation write different files and need not wait for each other,
        and nothing waits on work on a different structure."""
        with self._guard:
            return self._locks.setdefault(purpose, threading.Lock())


def safe_token(token: str) -> str:
    """A token usable as a directory name inside the buffer.

    Leading dots go too: ``..`` is all allowed characters, and as a directory
    name it is the buffer's parent.
    """
    token = re.sub(r"[^A-Za-z0-9_.-]", "_", str(token))[:64].lstrip(".")
    return token or "structure"


class StructureBuffer:
    """The most recent ``limit`` structures, least recently used evicted."""

    def __init__(self, root: str | os.PathLike, limit: int = 12):
        self.root = os.path.realpath(root)
        self.limit = limit
        os.makedirs(self.root, exist_ok=True)
        self._entries: OrderedDict[str, BufferedStructure] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def put(self, token: str, text: str, fmt: str | None = None) -> BufferedStructure:
        """Buffer a structure under ``token``; returns its entry.

        Same name, same text: the existing entry, with its pocket results.
        Same name, new text -- a structure edited or prepared again --
        replaces the old copy. Raises ``ValueError`` if the text holds no
        structure.
        """
        token = safe_token(token)
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        with self._lock:
            old = self._entries.get(token)
            if old is not None and old.digest == digest:
                self._entries.move_to_end(token)
                return old

        # Outside the lock: a large mmCIF entry takes a while to parse, and
        # nothing else should wait on that.
        structure = Structure.from_text(text, fmt)
        fmt = detect_format(text, fmt)

        with self._lock:
            old = self._entries.pop(token, None)
            if old is not None and old.digest == digest:  # raced with itself
                self._entries[token] = old
                return old
            work = self._workdir(token)
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work)
            path = os.path.join(work, "source." + fmt)
            with open(path, "w") as fh:
                fh.write(text)
            entry = BufferedStructure(token, fmt, path, work, digest, structure)
            self._entries[token] = entry
            self._evict()
        return entry

    def get(self, token: str) -> BufferedStructure | None:
        with self._lock:
            entry = self._entries.get(token)
            if entry is not None:
                self._entries.move_to_end(token)
            return entry

    def require(self, token: str) -> BufferedStructure:
        entry = self.get(token)
        if entry is None:
            raise NotFound("unknown structure %r" % token)
        return entry

    def close(self) -> None:
        with self._lock:
            self._entries.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def _workdir(self, token: str) -> str:
        work = os.path.realpath(os.path.join(self.root, token))
        if os.path.dirname(work) != self.root:  # never true after safe_token
            raise BadRequest("invalid structure name %r" % token)
        return work

    def _evict(self) -> None:
        while len(self._entries) > self.limit:
            _, entry = self._entries.popitem(last=False)
            shutil.rmtree(entry.workdir, ignore_errors=True)
