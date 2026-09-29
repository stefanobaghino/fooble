import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from fooble import crawl as crawl_mod
from fooble.crawl import Cache, Client, crawl, recipe_urls_from_sitemap

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://fooby.ch/en/recipes.html</loc></url>
<url><loc>https://fooby.ch/en/recipes/27468/goulash-with-squash</loc></url>
<url><loc>https://fooby.ch/de/rezepte/27468/gulasch-mit-kuerbis</loc></url>
<url><loc>https://fooby.ch/en/recipes/18457/cream-of-mushroom-soup</loc></url>
<url><loc>https://fooby.ch/en/recipes/27468/goulash-with-squash</loc></url>
<url><loc>https://fooby.ch/en/recipes/vegan-main-dishes.html</loc></url>
</urlset>"""


def test_recipe_urls_from_sitemap_keeps_english_recipes_once_in_order():
    assert recipe_urls_from_sitemap(SITEMAP) == {
        27468: "https://fooby.ch/en/recipes/27468/goulash-with-squash",
        18457: "https://fooby.ch/en/recipes/18457/cream-of-mushroom-soup",
    }


def test_cache_roundtrip(tmp_path: Path):
    cache = Cache(tmp_path)
    assert not cache.has(1)
    cache.put(1, "<html>é</html>")
    assert cache.has(1)
    assert cache.path(1).read_text(encoding="utf-8") == "<html>é</html>"
    assert cache.ids() == [1]
    cache.record(id=1, status=200)
    entry = json.loads((tmp_path / "crawl.jsonl").read_text().splitlines()[0])
    assert entry["id"] == 1 and entry["status"] == 200 and "ts" in entry


def fake_client(handler) -> Client:
    client = Client(delay=0)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_crawl_fetches_uncached_pages_and_logs(tmp_path: Path):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        if "18457" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, text=f"<html>{request.url.path}</html>")

    cache = Cache(tmp_path)
    cache.put(27468, "cached")
    results = list(crawl(tmp_path, client=fake_client(handler)))

    assert results == [(18457, 404)]
    assert cache.path(27468).read_text() == "cached", "cached page must not be refetched"
    assert not cache.has(18457)
    assert requested == [
        "https://fooby.ch/sitemap.xml",
        "https://fooby.ch/en/recipes/18457/cream-of-mushroom-soup",
    ]
    assert (tmp_path / "sitemap.xml").read_text() == SITEMAP


def test_crawl_refresh_limit_and_only(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(200, text="page")

    client = fake_client(handler)
    assert [r for r, _ in crawl(tmp_path, limit=1, client=client)] == [27468]
    assert [r for r, _ in crawl(tmp_path, client=client)] == [18457]
    assert list(crawl(tmp_path, client=client)) == []
    assert [r for r, _ in crawl(tmp_path, refresh=True, only=[18457], client=client)] == [18457]


def test_crawl_stops_after_consecutive_failures(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(crawl_mod, "MAX_CONSECUTIVE_FAILURES", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        raise httpx.ConnectError("down")

    with pytest.raises(RuntimeError, match="consecutive failures"):
        list(crawl(tmp_path, client=fake_client(handler)))
    entries = [json.loads(line) for line in (tmp_path / "crawl.jsonl").read_text().splitlines()]
    assert [e["status"] for e in entries] == [0, 0]


def test_crawl_does_not_count_404_as_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(crawl_mod, "MAX_CONSECUTIVE_FAILURES", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(404)

    results = list(crawl(tmp_path, client=fake_client(handler)))
    assert [status for _, status in results] == [404, 404]


def test_crawl_skips_recently_gone_pages_for_a_week(tmp_path: Path):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        requested.append(request.url.path)
        return httpx.Response(404 if "18457" in request.url.path else 410)

    client = fake_client(handler)
    t0 = datetime.now(UTC)
    assert [s for _, s in crawl(tmp_path, client=client, now=t0)] == [410, 404]
    assert list(crawl(tmp_path, client=client, now=t0 + timedelta(days=6))) == []
    assert [
        r for r, _ in crawl(tmp_path, refresh=True, client=client, now=t0 + timedelta(days=6))
    ] == [27468, 18457]
    requested.clear()
    assert [r for r, _ in crawl(tmp_path, client=client, now=t0 + timedelta(days=8))] == [
        27468,
        18457,
    ]
    assert len(requested) == 2


def test_recently_gone_counts_calendar_days(tmp_path: Path):
    cache = Cache(tmp_path)
    cache.record(id=1, status=404, ts="2026-09-21T01:21:26+00:00")

    def gone(ts: str) -> bool:
        return 1 in cache.recently_gone(datetime.fromisoformat(ts))

    # The next week's crawl started 16 minutes earlier in the night: still due.
    assert not gone("2026-09-28T01:05:01+00:00")
    assert gone("2026-09-27T01:59:00+00:00")
    # A manual daytime crawl, then the nightly one: days count, not hours.
    cache.record(id=2, status=404, ts="2026-09-21T14:00:00+00:00")
    assert 2 not in cache.recently_gone(datetime.fromisoformat("2026-09-28T01:00:00+00:00"))


def test_crawl_retries_gone_page_whose_last_answer_was_not_gone(tmp_path: Path):
    cache = Cache(tmp_path)
    cache.record(id=18457, status=404)
    cache.record(id=18457, status=503)
    assert cache.recently_gone(datetime.now(UTC)) == set()
    cache.record(id=18457, status=404)
    assert cache.recently_gone(datetime.now(UTC)) == {18457}


def test_client_retries_on_server_error(monkeypatch):
    monkeypatch.setattr(crawl_mod.time, "sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls < 3 else 200, text="ok")

    assert fake_client(handler).get("https://fooby.ch/x").status_code == 200
    assert calls == 3
