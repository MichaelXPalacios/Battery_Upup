"""Append-only download manifest (ERCOT allows 3 downloads of the same report per 12 months via its API)."""
import csv
import hashlib
import threading
from datetime import datetime, timezone

from .paths import MANIFEST

FIELDS = ["source", "report_id", "url", "file", "bytes", "sha256", "downloaded_at_utc", "method"]
_LOCK = threading.Lock()


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def _rows():
    if not MANIFEST.exists():
        return []
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def already_have(key):
    """True if a url (or a doclookupId / file name contained in one) is already in the manifest."""
    key = str(key)
    return any(key == r["url"] or key in r["url"] or key == r["file"] for r in _rows())


def downloads_of(report_id, method="api"):
    """How many times a report has been pulled by a method (watch the 3-per-12-months API limit)."""
    return sum(1 for r in _rows() if r["report_id"] == report_id and r["method"] == method)


def record(source, report_id, url, path, method):
    """Append one row; never store credentials or tokens in url."""
    row = {"source": source, "report_id": report_id, "url": url, "file": str(path),
           "bytes": path.stat().st_size, "sha256": sha256(path),
           "downloaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "method": method}
    with _LOCK:
        new = not MANIFEST.exists()
        with open(MANIFEST, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
    return row
