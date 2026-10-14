"""The immutable raw lake (PRD FR2, DESIGN.md Storage).

    DISPATCH_HOME/lake/raw/<source>/<report_id>/<YYYY-MM-DD of publish, UTC>/<original file name>
    DISPATCH_HOME/lake/catalog.jsonl     append only: one JSON line per raw file

Rules
* A raw file is written once and never changed or deleted. Writing the same file name with the same sha256 again is a
  no-op that returns the existing path. The same name with different bytes raises `LakeConflictError`.
* Uniqueness is (source, report_id, file name): ERCOT's constructed names embed the posting timestamp, so a re-published
  report is a new name and is kept next to the old one.
* Every new file appends exactly one catalog line: source, report_id, published_utc, ingested_utc, path, bytes, sha256,
  origin. `path` is relative to the lake directory, so a lake can be copied or moved.
* The catalog is the index. `iter_raw` reads it; nothing here scans the directory tree.

Paths are read from `dispatch.paths` at call time (not at import time) so the test fixture that re-points DISPATCH_HOME works.

Performance note: on this Windows machine every `open()` costs ~10 ms (real-time file scanning), so `put` opens each file as
few times as it can: the source is read once, the data file is written outside the catalog lock, and the catalog index,
the lock file and the catalog append handle are kept open between calls.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Union

import pandas as pd

from .. import paths
from . import reports
from .times import iso_z, parse_mis_publish, utc  # noqa: F401  (parse_mis_publish is re-exported: the lake owns the rule)

__all__ = ["put", "iter_raw", "RawFile", "LakeConflictError", "parse_mis_publish", "catalog_entries", "verify",
           "raw_path", "relpath", "has"]

ORIGINS = ("mis", "api", "web")                          # plus "adopt:<path>"
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")     # one path component: no separators, no leading dot
FIELDS = ("source", "report_id", "published_utc", "ingested_utc", "path", "bytes", "sha256", "origin")
_STREAM_ABOVE = 64 << 20                                  # files above 64 MB are streamed instead of read into memory


class LakeConflictError(Exception):
    """The same (source, report_id, file name) was offered with different bytes. Raw files are immutable."""


@dataclass(frozen=True)
class RawFile:
    source: str
    report_id: str
    published_utc: pd.Timestamp
    ingested_utc: str
    path: Path                     # absolute
    filename: str
    bytes: int
    sha256: str
    origin: str

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


# ------------------------------------------------------------------------------------------------ catalog state per lake
class _State:
    """Everything the process keeps open or cached for one catalog: lock handle, append handle, and the in-memory index."""

    def __init__(self, catalog: Path):
        self.catalog = catalog
        self.tlock = threading.RLock()
        self.lock_fh = None
        self.append_fh = None
        self.offset = 0
        self.entries: list[dict] = []
        self.by_key: dict[tuple, dict] = {}

    # --- cross-process lock on byte 0 of lake/.catalog.lock, held while the thread lock is held
    def _lock_file(self):
        if self.lock_fh is None:
            p = self.catalog.parent / ".catalog.lock"
            p.parent.mkdir(parents=True, exist_ok=True)
            self.lock_fh = open(p, "a+b")
            if os.path.getsize(p) == 0:
                self.lock_fh.write(b"0")
                self.lock_fh.flush()
        return self.lock_fh

    @contextlib.contextmanager
    def locked(self, timeout_s: float = 120.0):
        with self.tlock:
            f = self._lock_file()
            deadline = time.monotonic() + timeout_s
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"could not lock the lake catalog in {timeout_s}s")
                        time.sleep(0.01)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                self.refresh()
                yield self
            finally:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _add(self, e: dict):
        self.entries.append(e)
        self.by_key[(e["source"], e["report_id"], Path(e["path"]).name)] = e

    def refresh(self):
        """Read any bytes appended to the catalog since the last read (by us or another process)."""
        try:
            size = self.catalog.stat().st_size
        except FileNotFoundError:
            self.offset, self.entries, self.by_key = 0, [], {}
            return
        if size < self.offset:                           # replaced or truncated: start over
            self.offset, self.entries, self.by_key = 0, [], {}
        if size == self.offset:
            return
        with open(self.catalog, "rb") as f:
            f.seek(self.offset)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:                                      # only a partial line so far (another writer is mid-append)
            return
        for line in chunk[: end + 1].splitlines():
            line = line.strip()
            if line:
                self._add(json.loads(line.decode("utf-8")))
        self.offset += end + 1

    def append(self, entry: dict):
        """Append one line (the caller holds the lock and has just refreshed)."""
        if self.append_fh is None:
            self.catalog.parent.mkdir(parents=True, exist_ok=True)
            self.append_fh = open(self.catalog, "ab")
        line = (json.dumps({k: entry[k] for k in FIELDS}, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        self.append_fh.write(line)
        self.append_fh.flush()
        os.fsync(self.append_fh.fileno())
        self._add({k: entry[k] for k in FIELDS})
        self.offset += len(line)


_STATES: dict[str, _State] = {}
_STATES_GUARD = threading.Lock()


def _state() -> _State:
    key = str(paths.CATALOG)
    with _STATES_GUARD:
        st = _STATES.get(key)
        if st is None:
            st = _STATES[key] = _State(Path(paths.CATALOG))
    return st


def _fresh_state() -> _State:
    """The state with its index brought up to date (cheap when nothing was appended: one stat call)."""
    st = _state()
    with st.tlock:
        st.refresh()
    return st


# ------------------------------------------------------------------------------------------------ helpers
def relpath(source: str, report_id: str, published_utc: pd.Timestamp, filename: str) -> str:
    d = utc(published_utc).strftime("%Y-%m-%d")
    return f"raw/{source}/{report_id}/{d}/{filename}"


def raw_path(source: str, report_id: str, published_utc: pd.Timestamp, filename: str) -> Path:
    return paths.LAKE / relpath(source, report_id, published_utc, filename)


def _check_component(label: str, value: str) -> str:
    if not isinstance(value, str) or not _SAFE.match(value) or value in (".", ".."):
        raise ValueError(f"bad {label} {value!r}: use one plain path component (letters, digits, . _ + -)")
    return value


def _check_origin(origin: str) -> str:
    if origin in ORIGINS or (isinstance(origin, str) and origin.startswith("adopt:") and len(origin) > 6):
        return origin
    raise ValueError(f"origin must be one of {ORIGINS} or 'adopt:<path>', got {origin!r}")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(data_or_path) -> tuple[Union[bytes, Path], str, int]:
    """(payload, sha256, size). Payload is bytes (read once) or, for very large files, the source path to stream from."""
    if isinstance(data_or_path, (bytes, bytearray, memoryview)):
        b = bytes(data_or_path)
        return b, hashlib.sha256(b).hexdigest(), len(b)
    p = Path(data_or_path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    size = p.stat().st_size
    if size > _STREAM_ABOVE:
        return p, _file_sha256(p), size
    b = p.read_bytes()
    return b, hashlib.sha256(b).hexdigest(), len(b)


def _write_new(dest: Path, payload: Union[bytes, Path]) -> bool:
    """Create `dest` without ever replacing a file: temp file in the same folder, then an atomic no-overwrite hard link.
    Returns True if this call created it, False if it already existed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".tmp{os.getpid()}-{threading.get_ident()}")      # short: Windows paths top out at 260 characters
    try:
        with open(tmp, "wb") as out:
            if isinstance(payload, (bytes, bytearray)):
                out.write(payload)
            else:
                with open(payload, "rb") as src:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(tmp, dest)                           # atomic and fails if dest exists
        except FileExistsError:
            return False
        except OSError:                                  # file system without hard links
            if dest.exists():
                return False
            os.rename(tmp, dest)
        with contextlib.suppress(OSError):
            os.chmod(dest, stat.S_IREAD)                 # read-only attribute: a cheap guard against accidental edits
        return True
    finally:
        with contextlib.suppress(OSError):
            os.chmod(tmp, stat.S_IWRITE)
            os.unlink(tmp)


def _entry_to_raw(e: dict) -> RawFile:
    return RawFile(source=e["source"], report_id=e["report_id"], published_utc=utc(e["published_utc"]),
                   ingested_utc=e["ingested_utc"], path=paths.LAKE / e["path"], filename=Path(e["path"]).name,
                   bytes=int(e["bytes"]), sha256=e["sha256"], origin=e["origin"])


def _conflict(source, report_id, filename, have_sha, sha) -> LakeConflictError:
    return LakeConflictError(
        f"{source}/{report_id}/{filename}: lake already holds different bytes (sha256 {have_sha[:12]}..., offered {sha[:12]}...). "
        f"Raw files are immutable.")


# ------------------------------------------------------------------------------------------------ public API
def put(source: str, report_id: str, published_utc, filename: str, data_or_path: Union[bytes, str, Path], origin: str,
        *, ingested_utc=None) -> Path:
    """Store one raw file. Returns its absolute path in the lake.

    * `published_utc`: when the data became public (tz-aware; for ERCOT MIS use `parse_mis_publish(filename)`).
      It picks the date folder (UTC date) and goes into the catalog.
    * same (source, report_id, filename) and same sha256: no-op, returns the existing path, no new catalog line.
    * same name, different bytes: raises LakeConflictError. The existing file is left alone.
    * `data_or_path`: bytes, or the path of a file to copy (the original is never moved or edited).
    Thread- and process-safe.
    """
    _check_component("source", source)
    report_id = reports.canonical_id(report_id)
    _check_component("report_id", report_id)
    _check_component("filename", filename)
    origin = _check_origin(origin)
    pub = utc(published_utc)
    key = (source, report_id, filename)
    st = _fresh_state()

    payload, sha, size = _load(data_or_path)
    have = st.by_key.get(key)                            # already cataloged (the common case when re-adopting)
    if have is not None:
        p = paths.LAKE / have["path"]
        if have["sha256"] != sha:
            raise _conflict(source, report_id, filename, have["sha256"], sha)
        if not p.exists():                               # cataloged but missing on disk: restore the identical bytes
            _write_new(p, payload)
        return p

    dest = raw_path(source, report_id, pub, filename)
    created = _write_new(dest, payload)                  # outside the catalog lock: this is the slow part
    if not created and _file_sha256(dest) != sha:        # on disk but not cataloged, with other bytes
        raise LakeConflictError(f"{dest} exists with different bytes and is not in the catalog. Raw files are immutable.")
    with st.locked():
        have = st.by_key.get(key)                        # another thread or process may have cataloged it meanwhile
        if have is not None:
            if have["sha256"] != sha:
                raise _conflict(source, report_id, filename, have["sha256"], sha)
            return paths.LAKE / have["path"]
        st.append({"source": source, "report_id": report_id, "published_utc": iso_z(pub),
                   "ingested_utc": iso_z(ingested_utc if ingested_utc is not None else pd.Timestamp.now(tz="UTC")),
                   "path": relpath(source, report_id, pub, filename), "bytes": size, "sha256": sha, "origin": origin})
    return dest


def has(source: str, report_id: str, filename: str) -> bool:
    """True if the catalog already lists this (source, report_id, filename)."""
    return (source, reports.canonical_id(report_id), filename) in _fresh_state().by_key


def catalog_entries(report_id: Optional[str] = None, source: Optional[str] = None) -> list[dict]:
    """Copies of the catalog lines, in file order, optionally filtered."""
    rid = reports.canonical_id(report_id) if report_id is not None else None
    return [dict(e) for e in _fresh_state().entries
            if (rid is None or e["report_id"] == rid) and (source is None or e["source"] == source)]


def iter_raw(report_id: str, published_before=None, *, source: Optional[str] = None) -> Iterator[RawFile]:
    """Raw files of one report, oldest publish first (ties by file name).

    `published_before` (tz-aware): keep files with published_utc <= published_before. Inclusive, to match the platform
    rule that a row may feed a decision at asof when published_utc <= asof.
    """
    rid = reports.canonical_id(report_id)
    cut = utc(published_before) if published_before is not None else None
    last = {}
    for e in _fresh_state().entries:                     # a name can appear twice only after hand-editing the catalog
        if e["report_id"] == rid and (source is None or e["source"] == source):
            last[(e["source"], Path(e["path"]).name)] = e
    out = [_entry_to_raw(e) for e in last.values()]
    out.sort(key=lambda r: (r.published_utc, r.filename))
    for r in out:
        if cut is None or r.published_utc <= cut:
            yield r


def verify(report_id: Optional[str] = None, check_hash: bool = False) -> list[str]:
    """Problems found in the lake: cataloged files that are missing or the wrong size (or hash if check_hash)."""
    problems = []
    for e in catalog_entries(report_id):
        p = paths.LAKE / e["path"]
        if not p.exists():
            problems.append(f"missing: {e['path']}")
        elif p.stat().st_size != e["bytes"]:
            problems.append(f"size differs: {e['path']}")
        elif check_hash and _file_sha256(p) != e["sha256"]:
            problems.append(f"sha256 differs: {e['path']}")
    return problems


def close_all() -> None:
    """Release the catalog handles this process keeps open (tests and long-lived services call this on shutdown)."""
    with _STATES_GUARD:
        for st in _STATES.values():
            with st.tlock:
                for fh in (st.append_fh, st.lock_fh):
                    if fh is not None:
                        with contextlib.suppress(Exception):
                            fh.close()
                st.append_fh = st.lock_fh = None
        _STATES.clear()


import atexit as _atexit  # noqa: E402

_atexit.register(close_all)
