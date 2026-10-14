"""The halt flag (PRD: "POST /kill sets the Redis halt flag; the guard rejects every setpoint until cleared").

`RedisHaltFlag` implements `contracts.HaltFlag` on a single Redis key, `config.HALT_KEY` ("dispatch:halt"), whose value is the
JSON object {"reason", "set_by", "set_utc"}. The flag is "set" when the key exists. That keeps it usable from anywhere:

    redis-cli SET dispatch:halt '{"reason":"manual","set_by":"ops","set_utc":"2026-10-10T15:00:00Z"}'    # halts
    redis-cli DEL dispatch:halt                                                                        # resumes

Fail closed. If Redis cannot be reached, `is_set()` answers (True, "redis_unreachable: ..."): no answer is treated as a halt.
A kill that cannot reach Redis is latched in this process, so it is not lost when Redis comes back; the latch is written
back to Redis on the next read that gets an answer, and from then on it is ordinary Redis state. Only Redis commands that Redis 3.0 has are used (GET, SET, DEL, PING).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

import pandas as pd

from .. import config

log = logging.getLogger("dispatch.service")

REDIS_TIMEOUT_S = 1.0          # ASSUMPTION: a Redis that does not answer within a second counts as down (fail closed)
PROBE_KEY = config.HALT_KEY + ":probe"
PROBE_CACHE_S = 30.0           # ASSUMPTION: how long a write probe's answer is reused
UNREACHABLE = "redis_unreachable"


class HaltUnavailable(RuntimeError):
    """The halt flag could not be changed (Redis is unreachable). Dispatch stays halted."""


def redact_url(url: str) -> str:
    """host:port/db of a Redis URL with any credentials removed, for logs and /health."""
    try:
        p = urlsplit(url)
        return f"{p.scheme}://{p.hostname or ''}{':' + str(p.port) if p.port else ''}{p.path}"
    except Exception:
        return "<unparseable redis url>"


def normalize_redis_url(url: str) -> str:
    """The same URL with `localhost` replaced by `127.0.0.1`.

    I1, 10 Oct 2026: on this Windows machine `localhost` resolves to `::1` first and the platform's Redis (`scripts/redis_dispatch.sh`, `--bind
    127.0.0.1`) does not listen there, so EVERY new connection waited about 2.0 s for the IPv6 refusal before falling back to IPv4 (measured: 2.011 s to
    `localhost:6390`, 0.001 s to `127.0.0.1:6390`). Two of the 2.2 s of a real cycle were that wait. With the halt flag's 1 s connect timeout it also
    made a reconnect cost a second. Only the host is touched; credentials, port and database stay."""
    try:
        p = urlsplit(url)
        if (p.hostname or "").lower() != "localhost":
            return url
        auth = ""
        if p.username or p.password:
            auth = (p.username or "") + (":" + p.password if p.password else "") + "@"
        return p._replace(netloc=f"{auth}127.0.0.1" + (f":{p.port}" if p.port else "")).geturl()
    except Exception:
        return url


def make_redis_client(url: Optional[str] = None):
    """A redis-py client with short timeouts. It does not connect until the first command.

    protocol=2 is required: redis-py 8 opens a connection with HELLO (RESP3), which Redis 3.0 (the local Redis on this
    machine is 3.0.504) answers with "unknown command 'HELLO'", so without it nothing connects."""
    import redis
    return redis.Redis.from_url(normalize_redis_url(url or config.REDIS_URL), protocol=2, socket_timeout=REDIS_TIMEOUT_S,
                                socket_connect_timeout=REDIS_TIMEOUT_S, decode_responses=True)


class RedisHaltFlag:
    def __init__(self, client=None, *, key: str = config.HALT_KEY, url: Optional[str] = None,
                 clock: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC")):
        self.key = key
        self._client = client if client is not None else make_redis_client(url)
        self._clock = clock
        self._latch: Optional[dict] = None         # a kill this process accepted, kept until a clear succeeds
        self._lock = threading.RLock()
        self.last_set_persisted = True
        self._probe: Optional[tuple] = None        # (monotonic time, writable)

    # -- HaltFlag protocol
    def is_set(self) -> tuple:
        """(halted, reason). Never raises. Redis trouble is a halt."""
        with self._lock:
            try:
                raw = self._client.get(self.key)
            except Exception as exc:
                return True, f"{UNREACHABLE}: {type(exc).__name__}"
            if raw is None:
                if self._latch is None:
                    return False, ""
                latched = self._latch              # a kill Redis refused earlier: try again now that Redis answers
                if self._persist(latched):
                    self._latch = None             # from here it is ordinary Redis state (redis-cli DEL resumes it)
                return True, str(latched.get("reason", "halt flag set"))
            try:
                info = json.loads(raw)
                return True, str(info.get("reason", "")) or "halt flag set"
            except (ValueError, AttributeError):
                return True, "halt flag present but unreadable"

    def set(self, reason: str, set_by: str) -> None:
        info = {"reason": str(reason), "set_by": str(set_by), "set_utc": self._clock().tz_convert("UTC").isoformat().replace("+00:00", "Z")}
        with self._lock:
            self.last_set_persisted = self._persist(info)
            self._latch = None if self.last_set_persisted else info    # kept in this process only while Redis refuses it

    def clear(self, reason: str, cleared_by: str) -> None:
        with self._lock:
            try:
                self._client.delete(self.key)
            except Exception as exc:
                raise HaltUnavailable(f"cannot clear the halt flag, Redis is unreachable ({type(exc).__name__}); dispatch stays halted") from exc
            self._latch = None
            log.warning("halt flag cleared by %s: %s", cleared_by, reason)

    # -- extras for /health and the API
    def info(self) -> dict:
        """{"halted", "reason", "set_by", "set_utc", "redis_ok", "persisted", "writable"} without raising."""
        with self._lock:
            try:
                raw = self._client.get(self.key)
            except Exception as exc:
                return {"halted": True, "reason": f"{UNREACHABLE}: {type(exc).__name__}", "set_by": "", "set_utc": "",
                        "redis_ok": False, "persisted": False, "writable": False}
            if raw is None:
                if self._latch is None:
                    return {"halted": False, "reason": "", "set_by": "", "set_utc": "", "redis_ok": True, "persisted": True,
                            "writable": self.writable()}
                return {"halted": True, "persisted": False, "redis_ok": True, "writable": self.writable(), **_pick(self._latch)}
            try:
                return {"halted": True, "persisted": True, "redis_ok": True, "writable": self.writable(), **_pick(json.loads(raw))}
            except (ValueError, AttributeError):
                return {"halted": True, "reason": "halt flag present but unreadable", "set_by": "", "set_utc": "",
                        "redis_ok": True, "persisted": True, "writable": self.writable()}

    def writable(self, max_age_s: float = PROBE_CACHE_S) -> bool:
        """Can Redis take a write? A Redis at its maxmemory with noeviction (the local one is) answers reads and refuses SET, which
        means a kill made with `redis-cli SET dispatch:halt ...` would fail. Probed with a scratch key, cached for max_age_s."""
        with self._lock:
            now = time.monotonic()
            if self._probe is not None and now - self._probe[0] < max_age_s:
                return self._probe[1]
            try:
                self._client.set(PROBE_KEY, "1")
                self._client.delete(PROBE_KEY)
                ok = True
            except Exception:
                ok = False
            self._probe = (now, ok)
            return ok

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def _persist(self, info: dict) -> bool:
        try:
            self._client.set(self.key, json.dumps(info, sort_keys=True))
            return True
        except Exception as exc:
            log.error("halt flag set locally but not written to Redis: %s", type(exc).__name__)
            return False


def _pick(info: dict) -> dict:
    return {"reason": str(info.get("reason", "")), "set_by": str(info.get("set_by", "")), "set_utc": str(info.get("set_utc", ""))}
