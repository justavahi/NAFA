"""Polite HTTP client: identifying User-Agent, per-host throttling, robots.txt, bounded retries with growing pauses,
manual redirects with a domain whitelist. No proxy rotation, no anti-bot evasion (RSK-10/15)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .config import get_config


class FetchError(Exception):
    def __init__(self, error_type: str, message: str = "", status: int | None = None, attempts: int = 0):
        super().__init__(f"{error_type}: {message}")
        self.error_type, self.message, self.status, self.attempts = error_type, message, status, attempts


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content: bytes
    headers: dict
    attempts: int
    elapsed_s: float
    robots: str = "not_checked"                 # allowed | disallowed | unreachable | not_checked


def host_allowed(host: str, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in (a.lower() for a in allowed))


class PoliteClient:
    def __init__(self, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        cfg = get_config()
        self.cfg = cfg
        contact = os.getenv("NAFA_CONTACT", "team@example.invalid")
        self.user_agent = cfg.get("http.user_agent_template").format(contact=contact)
        self.robots_token = cfg.get("http.robots_token", "NAFA-prototype")
        self._sleep, self._clock = sleep, clock
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept": "*/*"},
            timeout=cfg.get("http.timeout_s", 20), follow_redirects=False, transport=transport,
        )
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, tuple[float, RobotFileParser | str]] = {}

    def close(self) -> None:
        self._client.close()

    # ---------- robots.txt ----------
    def robots_status(self, url: str) -> str:
        """Returns allowed | disallowed | unreachable. 'unreachable' is resolved by http.robots.on_unreachable."""
        p = urlparse(url)
        key = f"{p.scheme}://{p.netloc}"
        ttl = self.cfg.get("http.robots.ttl_s", 3600)
        cached = self._robots.get(key)
        if not cached or self._clock() - cached[0] > ttl:
            cached = (self._clock(), self._load_robots(key))
            self._robots[key] = cached
        parser = cached[1]
        if isinstance(parser, str):
            return parser
        return "allowed" if parser.can_fetch(self.robots_token, url) else "disallowed"

    def _load_robots(self, base: str) -> RobotFileParser | str:
        rp = RobotFileParser()
        try:
            self._throttle(urlparse(base).netloc)
            r = self._client.get(base + "/robots.txt", follow_redirects=True)
        except httpx.HTTPError:
            return "unreachable"
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
            return rp
        if r.status_code in (401, 403):
            return "disallowed"                   # same convention as urllib.robotparser
        if 400 <= r.status_code < 500:
            rp.parse([])                          # no robots.txt -> everything allowed
            return rp
        return "unreachable"

    def _robots_ok(self, url: str) -> str:
        if not self.cfg.get("http.robots.enabled", True):
            return "not_checked"
        st = self.robots_status(url)
        if st == "unreachable" and self.cfg.get("http.robots.on_unreachable", "allow") == "deny":
            raise FetchError("robots_unreachable", url)
        if st == "disallowed":
            raise FetchError("robots_disallowed", url)
        return st

    # ---------- requests ----------
    def _throttle(self, host: str) -> None:
        gap = self.cfg.get("http.min_interval_s", 1.0)
        last = self._last_hit.get(host)
        if last is not None:
            wait = gap - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_hit[host] = self._clock()

    def get(self, url: str, *, params: dict | None = None, allowed_domains: list[str] | None = None,
            check_robots: bool = True, headers: dict | None = None) -> FetchResult:
        max_redirects = self.cfg.get("http.max_redirects", 3)
        t0 = self._clock()
        total_attempts = 0
        robots = "not_checked"
        for _hop in range(max_redirects + 1):
            p = urlparse(url)
            if p.scheme not in ("http", "https"):
                raise FetchError("bad_scheme", url)
            if not host_allowed(p.hostname or "", allowed_domains):
                raise FetchError("domain_not_allowed", p.hostname or "")
            if check_robots:
                robots = self._robots_ok(url)
            resp, attempts = self._request_with_retries(url, params, headers)
            total_attempts += attempts
            params = None                          # only for the first hop
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            if len(resp.content) > self.cfg.get("http.max_bytes", 10_000_000):
                raise FetchError("too_large", url, resp.status_code, total_attempts)
            return FetchResult(url, str(resp.url), resp.status_code, resp.content, dict(resp.headers),
                               total_attempts, self._clock() - t0, robots)
        raise FetchError("too_many_redirects", url, attempts=total_attempts)

    def _request_with_retries(self, url, params, headers) -> tuple[httpx.Response, int]:
        retries = self.cfg.get("http.max_retries", 5)
        base = self.cfg.get("http.backoff_base_s", 2.0)
        cap = self.cfg.get("http.backoff_max_s", 60)
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        last_status: int | None = None
        for attempt in range(1, retries + 2):
            self._throttle(host)
            try:
                resp = self._client.get(url, params=params, headers=headers)
                last_status = resp.status_code
                if resp.status_code == 429 or resp.status_code >= 500:
                    delay = min(cap, base * 2 ** (attempt - 1))
                    ra = resp.headers.get("retry-after")
                    if ra and ra.isdigit():
                        delay = min(cap, max(delay, int(ra)))
                    if attempt <= retries:
                        self._sleep(delay)
                        continue
                    raise FetchError("http_error", f"status {resp.status_code}", resp.status_code, attempt)
                if 400 <= resp.status_code < 500:
                    raise FetchError("http_error", f"status {resp.status_code}", resp.status_code, attempt)
                return resp, attempt
            except httpx.TimeoutException as e:
                last_exc = e
                kind = "timeout"
            except httpx.TransportError as e:
                last_exc = e
                kind = "network"
            if attempt <= retries:
                self._sleep(min(cap, base * 2 ** (attempt - 1)))
                continue
            raise FetchError(kind, str(last_exc), last_status, attempt)
        raise FetchError("network", "unreachable code", attempts=retries + 1)  # pragma: no cover
