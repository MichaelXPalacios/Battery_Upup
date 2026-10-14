"""The append-only audit log (PRD FR10): every signal, guard decision, setpoint, kill, resume, halt, error and model load.

One JSON object per line, in daily files `DISPATCH_HOME/runtime/audit/audit-YYYYMMDD.jsonl`. Each record carries the SHA-256
of the previous record (`prev_hash`) and its own (`hash`), so editing, deleting, inserting or reordering a record breaks
the chain at that line and `verify()` says where. The chain runs across daily files: the first record of a day points at
the last record of the day before.

    {"schema":1, "seq":12, "ts_utc":"2026-10-10T15:07:30.123456Z", "type":"guard_decision", "asset_id":"PILOT_HB_NORTH",
     ...event fields..., "prev_hash":"<64 hex>", "hash":"<64 hex>"}

hash = sha256 of the canonical JSON of the record without the `hash` field (sorted keys, no spaces, ASCII). The first
record has seq 0 and prev_hash = GENESIS_HASH.

What this does and does not give you
  - Append only in the code: the file is opened with mode "ab"; there is no update or delete, and no rotation that drops
    records. Retention is an OPEN QUESTION in the PRD, so `AUDIT_RETENTION_DAYS` is None and nothing ever expires.
  - Tamper evidence, not tamper proof: someone who can rewrite the whole file can rebuild a valid chain. Truncating the
    tail is invisible to the chain alone, so `head()` returns (seq, hash) for an external anchor, and
    `verify(expected_head=...)` fails if the log is behind or disagrees with that anchor (the service keeps the anchor in
    Redis; see service/app.py).
  - One writer per directory. A lock serialises threads; two processes appending to one directory would fork the chain
    (the next append re-reads the tail and `verify()` flags any fork).
  - Durable: each append is flushed and fsynced before it returns, so a record written ahead of an action survives a crash
    of that action. If the tail is torn (a crash in the middle of a line) appends refuse until an operator repairs it:
    no audit record means no dispatch.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

from .. import paths

# ---- named constants
AUDIT_RETENTION_DAYS = None        # OPEN QUESTION (PRD, "Audit log retention period"): undecided. None = keep everything; no code deletes or ages out a record.
SCHEMA = 1
GENESIS_HASH = "0" * 64
EVENT_TYPES = (
    "signal",              # what the forecast + optimizer proposed (plan summary, proposed setpoint)
    "guard_decision",      # the guard's verdict with the state it saw, written BEFORE any execution
    "setpoint",            # a setpoint executed on the paper ledger
    "cycle",               # one-line summary of a /signal cycle: status, reasons, latency
    "kill",                # POST /kill
    "resume",              # POST /resume
    "halt",                # an automatic halt (monitoring) or a halt flag found set
    "error",               # an exception or an unavailable dependency
    "model_load",          # a model loaded (or failed to load) from the registry
    "breach",              # a monitoring check breached
    "retrain_request",     # monitoring asked for a retrain (recorded only; nothing trains)
    "gate3",               # a Gate 3 evaluation (paper replay against a backtest) and what was recorded in the registry
    "startup",             # service started
)
RESERVED = ("schema", "seq", "ts_utc", "prev_hash", "hash")
MAX_RECORD_BYTES = 64 * 1024       # ASSUMPTION: a larger payload is replaced by its size and sha256 (evidence kept, disk protected)
FILE_PREFIX = "audit-"
FILE_SUFFIX = ".jsonl"
_REQUIRED = ("schema", "seq", "ts_utc", "type", "prev_hash", "hash")


class AuditError(RuntimeError):
    """The audit log cannot be written or is corrupt. The caller must not dispatch."""


@dataclasses.dataclass(frozen=True)
class VerifyReport:
    ok: bool
    first_bad_line: Optional[int]      # 1-based, counted across all daily files in order
    file: Optional[str]
    reason: str
    records: int
    head_seq: int
    head_hash: str


# ---- JSON helpers
def to_jsonable(obj: Any, _depth: int = 0) -> Any:
    """Turn setpoints, decisions, timestamps, numpy scalars and NaN into plain JSON (NaN and inf become strings)."""
    if _depth > 12:
        return "<max depth>"
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        if math.isinf(obj):
            return "Infinity" if obj > 0 else "-Infinity"
        return obj
    mod = type(obj).__module__
    if mod.startswith("pandas"):
        import pandas as pd
        if obj is pd.NaT:
            return None
        if isinstance(obj, pd.Timestamp):
            return _iso(obj.to_pydatetime(warn=False) if obj.tzinfo is None else obj.tz_convert("UTC").to_pydatetime())
        if isinstance(obj, pd.Timedelta):
            return obj.total_seconds()
        if isinstance(obj, pd.Series):
            return {str(k): to_jsonable(v, _depth + 1) for k, v in obj.items()}
        if isinstance(obj, pd.DataFrame):
            return {"_dataframe": {"rows": int(len(obj)), "columns": [str(c) for c in obj.columns]}}
    if mod.startswith("numpy"):
        import numpy as np
        if isinstance(obj, np.generic):
            return to_jsonable(obj.item(), _depth + 1)
        if isinstance(obj, np.ndarray):
            return [to_jsonable(v, _depth + 1) for v in obj.tolist()]
    if isinstance(obj, datetime):
        return _iso(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name), _depth + 1) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v, _depth + 1) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((to_jsonable(v, _depth + 1) for v in obj), key=str)
    if isinstance(obj, Path):
        return obj.as_posix()
    return repr(obj)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.isoformat()
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(record: Mapping) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def record_hash(record: Mapping) -> str:
    """SHA-256 of the canonical JSON of the record without its `hash` field."""
    return hashlib.sha256(canonical({k: v for k, v in record.items() if k != "hash"})).hexdigest()


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


# ---- the log
class AuditLog:
    def __init__(self, directory: Optional[Path] = None, *, clock: Callable[[], datetime] = _default_clock,
                 fsync: bool = True, anchor: Optional[Callable[[int, str], None]] = None):
        self.dir = Path(directory) if directory is not None else Path(paths.AUDIT)    # read at call time: tests re-point DISPATCH_HOME
        self.dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._fsync = fsync
        self._anchor = anchor
        self._lock = threading.RLock()
        self._seq = -1
        self._hash = GENESIS_HASH
        self._file: Optional[Path] = None
        self._size = 0
        self._loaded = False

    # -- files
    def files(self) -> list:
        return sorted(p for p in self.dir.glob(f"{FILE_PREFIX}*{FILE_SUFFIX}") if p.is_file())

    def _path_for(self, ts: datetime) -> Path:
        path = self.dir / f"{FILE_PREFIX}{ts.astimezone(timezone.utc):%Y%m%d}{FILE_SUFFIX}"
        if self._file is not None and path.name < self._file.name:     # the clock went backwards: stay in the newest file
            return self._file
        return path

    def set_anchor(self, anchor: Optional[Callable[[int, str], None]]) -> None:
        """Install (or remove) the callback that receives (seq, hash) after every append."""
        self._anchor = anchor

    @property
    def has_anchor(self) -> bool:
        return self._anchor is not None

    # -- tail
    def _load_tail(self) -> None:
        """Find the last record on disk (checking its own hash) so the next append chains to it."""
        files = self.files()
        self._seq, self._hash, self._file, self._size = -1, GENESIS_HASH, None, 0
        for path in reversed(files):
            size = path.stat().st_size
            if size == 0:
                continue
            with open(path, "rb") as f:
                f.seek(max(0, size - 4 * MAX_RECORD_BYTES))
                chunk = f.read()
            if not chunk.endswith(b"\n"):
                raise AuditError(f"audit tail of {path.name} is torn (no newline at the end); repair it before dispatching")
            last = chunk.rstrip(b"\n").split(b"\n")[-1]
            try:
                rec = json.loads(last)
            except ValueError as exc:
                raise AuditError(f"audit tail of {path.name} is not valid JSON: {exc}") from exc
            if not isinstance(rec, dict) or rec.get("hash") != record_hash(rec) or not isinstance(rec.get("seq"), int):
                raise AuditError(f"audit tail of {path.name} fails its own hash; run verify()")
            self._seq, self._hash, self._file, self._size = rec["seq"], rec["hash"], path, size
            break
        self._loaded = True

    def head(self) -> tuple:
        """(seq, hash) of the newest record; (-1, GENESIS_HASH) for an empty log. Use it as an external anchor."""
        with self._lock:
            self._sync()
            return self._seq, self._hash

    def _sync(self) -> None:
        """Reload the tail if anything but this object changed the files since our last write."""
        if not self._loaded or self._file is None:
            self._load_tail()
            return
        try:
            size = self._file.stat().st_size
        except OSError:
            size = -1
        if size != self._size:
            self._load_tail()

    # -- append
    def append(self, event: Mapping) -> str:
        """Append one event and return its hash. `event` needs a `type` from EVENT_TYPES; other fields are free-form JSON."""
        etype = event.get("type") if isinstance(event, Mapping) else None
        if etype not in EVENT_TYPES:
            raise ValueError(f"audit event type must be one of {EVENT_TYPES}, got {etype!r}")
        clash = [k for k in RESERVED if k in event]
        if clash:
            raise ValueError(f"audit event may not set reserved fields {clash}")
        body = {str(k): to_jsonable(v) for k, v in event.items()}
        with self._lock:
            self._sync()
            now = self._clock()
            rec = {"schema": SCHEMA, "seq": self._seq + 1, "ts_utc": _iso(now), **body, "prev_hash": self._hash}
            rec["hash"] = record_hash(rec)
            line = canonical(rec)
            if len(line) > MAX_RECORD_BYTES:
                stub = {"schema": SCHEMA, "seq": rec["seq"], "ts_utc": rec["ts_utc"], "type": etype,
                        "asset_id": body.get("asset_id"), "truncated": True, "original_bytes": len(line),
                        "original_sha256": hashlib.sha256(line).hexdigest(), "prev_hash": self._hash}
                stub["hash"] = record_hash(stub)
                rec, line = stub, canonical(stub)
            path = self._path_for(now)
            try:
                with open(path, "ab") as f:
                    f.write(line + b"\n")
                    f.flush()
                    if self._fsync:
                        os.fsync(f.fileno())
                self._size = path.stat().st_size
            except OSError as exc:
                self._loaded = False                      # whatever happened on disk, re-read the tail next time
                raise AuditError(f"audit append failed: {exc}") from exc
            self._file, self._seq, self._hash = path, rec["seq"], rec["hash"]
            seq, h = self._seq, self._hash
        if self._anchor is not None:
            try:
                self._anchor(seq, h)
            except Exception:                              # an anchor is best effort and never blocks the log
                pass
        return h

    # -- read and verify
    def read(self, *, types: Optional[tuple] = None, asset_id: Optional[str] = None,
             limit: Optional[int] = None) -> Iterator[dict]:
        """Parsed records in order (no verification; use verify()). Unparseable lines are skipped."""
        n = 0
        for path in self.files():
            with open(path, "rb") as f:
                for raw in f:
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    if types and rec.get("type") not in types:
                        continue
                    if asset_id is not None and rec.get("asset_id") != asset_id:
                        continue
                    yield rec
                    n += 1
                    if limit is not None and n >= limit:
                        return

    def tail(self, n: int = 20, *, types: Optional[tuple] = None) -> list:
        recs = list(self.read(types=types))
        return recs[-n:]

    def recent(self, n: int) -> list:
        """The newest `n` parsed records, read from the end of the newest files (cheap on a long log). No verification."""
        out: list = []
        for path in reversed(self.files()):
            size = path.stat().st_size
            if size == 0:
                continue
            take = min(size, max(64 * 1024, n * 4096))              # a record is about 1 KB; read generously from the end
            with open(path, "rb") as f:
                f.seek(size - take)
                chunk = f.read()
            lines = chunk.split(b"\n")
            if take < size:
                lines = lines[1:]                                    # the first line is probably cut in half
            parsed = []
            for raw in lines:
                try:
                    parsed.append(json.loads(raw))
                except ValueError:
                    continue
            out = parsed + out
            if len(out) >= n:
                break
        return out[-n:]

    def verify_report(self, *, expected_head: Optional[tuple] = None) -> VerifyReport:
        """Walk every file in order and check structure, seq, prev_hash and hash of each line."""
        line_no, expect_seq, prev = 0, 0, GENESIS_HASH
        hash_at: dict = {}

        def bad(reason: str, path: Optional[Path]) -> VerifyReport:
            return VerifyReport(False, line_no, path.name if path else None, reason, max(expect_seq, 0), expect_seq - 1, prev)

        for path in self.files():
            data = path.read_bytes()
            if data and not data.endswith(b"\n"):
                lines = data.split(b"\n")
                torn = True
            else:
                lines = data.split(b"\n")[:-1] if data else []
                torn = False
            for i, raw in enumerate(lines):
                line_no += 1
                try:
                    rec = json.loads(raw)
                except ValueError:
                    return bad("not valid JSON", path)
                if not isinstance(rec, dict) or any(k not in rec for k in _REQUIRED):
                    return bad("missing required fields", path)
                if rec["seq"] != expect_seq:
                    return bad(f"seq {rec['seq']} where {expect_seq} was expected (record deleted, inserted or reordered)", path)
                if rec["prev_hash"] != prev:
                    return bad("prev_hash does not match the previous record (chain broken)", path)
                try:
                    recomputed = record_hash(rec)
                except (ValueError, TypeError, OverflowError, RecursionError):
                    # I1, 10 Oct 2026: a tampered number such as 262144.9998e05644 parses to inf, which `canonical` (allow_nan=False) refuses, so
                    # `verify` raised ValueError instead of reporting. A record that cannot even be hashed is an edited record.
                    return bad("hash does not match the record content (record edited; its content cannot be hashed)", path)
                if rec["hash"] != recomputed:
                    return bad("hash does not match the record content (record edited)", path)
                if torn and i == len(lines) - 1:
                    return bad("torn last line (no newline)", path)
                hash_at[expect_seq] = rec["hash"]
                prev, expect_seq = rec["hash"], expect_seq + 1
        if expected_head is not None:
            a_seq, a_hash = expected_head
            if a_seq >= expect_seq:
                line_no += 1
                return bad(f"log ends at seq {expect_seq - 1} but the anchor is at seq {a_seq} (tail truncated)", None)
            if a_seq >= 0 and hash_at.get(a_seq) != a_hash:
                return bad(f"record {a_seq} differs from the anchor", None)
        return VerifyReport(True, None, None, "ok", expect_seq, expect_seq - 1, prev)

    def verify(self, *, expected_head: Optional[tuple] = None) -> tuple:
        """(ok, first_bad_line). first_bad_line is 1-based across all files, or None when the chain is intact."""
        rep = self.verify_report(expected_head=expected_head)
        return rep.ok, rep.first_bad_line


def main(argv=None) -> int:                                  # python -m dispatch.execution.audit [verify|tail] [directory]
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "verify"
    log = AuditLog(Path(args[1]) if len(args) > 1 else None)
    if cmd == "tail":
        for rec in log.tail(20):
            print(json.dumps(rec, sort_keys=True))
        return 0
    rep = log.verify_report()
    print(f"audit {'OK' if rep.ok else 'BROKEN'}: {rep.records} records, head seq {rep.head_seq}"
          + ("" if rep.ok else f"; first bad line {rep.first_bad_line} in {rep.file}: {rep.reason}"))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
