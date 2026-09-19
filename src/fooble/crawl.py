"""Fetch English recipe pages from fooby.ch into a local HTML cache."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SITEMAP_URL = "https://fooby.ch/sitemap.xml"
RECIPE_URL_RE = re.compile(r"^https://fooby\.ch/en/recipes/(\d+)/[^/]+$")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36 fooble/0.1"
)
# robots.txt: Crawl-delay: 10
CRAWL_DELAY = 10.0
TIMEOUT = 30.0
SITEMAP_TIMEOUT = 300.0
SITEMAP_MAX_AGE = 24 * 3600
MAX_CONSECUTIVE_FAILURES = 5


def recipe_urls_from_sitemap(xml: str) -> dict[int, str]:
    """Return {recipe_id: url} for English recipe pages, in sitemap order."""
    out: dict[int, str] = {}
    for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
        m = RECIPE_URL_RE.match(loc.strip())
        if m:
            out.setdefault(int(m.group(1)), loc.strip())
    return out


class Cache:
    """On-disk cache: one HTML file per recipe id plus a JSONL fetch log."""

    def __init__(self, data_dir: Path):
        self.html_dir = data_dir / "html"
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = data_dir / "crawl.jsonl"

    def path(self, recipe_id: int) -> Path:
        return self.html_dir / f"{recipe_id}.html"

    def has(self, recipe_id: int) -> bool:
        return self.path(recipe_id).exists()

    def ids(self) -> list[int]:
        return sorted(int(p.stem) for p in self.html_dir.glob("*.html"))

    def put(self, recipe_id: int, html: str) -> None:
        tmp = self.path(recipe_id).with_suffix(".tmp")
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(self.path(recipe_id))

    def record(self, **entry: object) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


class Client:
    def __init__(self, delay: float = CRAWL_DELAY):
        self.delay = delay
        self._last = 0.0
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last = time.monotonic()

    def get(self, url: str) -> httpx.Response:
        backoff = self.delay
        for attempt in range(4):
            self._wait()
            resp = self._http.get(url)
            if resp.status_code in (429, 500, 502, 503, 504):
                log.warning("%s -> %s, retry %d", url, resp.status_code, attempt + 1)
                time.sleep(backoff)
                backoff *= 2
                continue
            return resp
        return resp

    def sitemap(self, cache_path: Path | None = None) -> str:
        """Fetch the (large) sitemap, reusing a cached copy younger than SITEMAP_MAX_AGE."""
        if cache_path and cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < SITEMAP_MAX_AGE:
                log.info("using cached sitemap (%.0f min old)", age / 60)
                return cache_path.read_text(encoding="utf-8")
        self._wait()
        resp = self._http.get(SITEMAP_URL, timeout=SITEMAP_TIMEOUT)
        resp.raise_for_status()
        if cache_path:
            cache_path.write_text(resp.text, encoding="utf-8")
        return resp.text


def crawl(
    data_dir: Path,
    limit: int | None = None,
    refresh: bool = False,
    only: Iterable[int] | None = None,
    client: Client | None = None,
) -> Iterator[tuple[int, int]]:
    """Fetch recipe pages not yet cached. Yields (recipe_id, status) per fetch."""
    client = client or Client()
    cache = Cache(data_dir)
    urls = recipe_urls_from_sitemap(client.sitemap(data_dir / "sitemap.xml"))
    log.info("sitemap lists %d English recipes", len(urls))

    todo = [rid for rid in urls if refresh or not cache.has(rid)]
    if only is not None:
        wanted = set(only)
        todo = [rid for rid in todo if rid in wanted]
    if limit is not None:
        todo = todo[:limit]
    log.info("%d recipes to fetch", len(todo))

    failures = 0
    for rid in todo:
        url = urls[rid]
        try:
            resp = client.get(url)
            status = resp.status_code
            if status == 200:
                cache.put(rid, resp.text)
                failures = 0
            elif status == 429 or status >= 500:
                failures += 1
            # Other 4xx (typically 404 for a recipe listed in the sitemap but
            # not published) are definitive answers, not transient failures.
            cache.record(id=rid, url=url, status=status, bytes=len(resp.content))
        except httpx.HTTPError as e:
            failures += 1
            status = 0
            cache.record(id=rid, url=url, status=0, error=str(e))
            log.warning("%s -> %s", url, e)
        yield rid, status
        if failures >= MAX_CONSECUTIVE_FAILURES:
            raise RuntimeError(f"{failures} consecutive failures, stopping")
