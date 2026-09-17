"""Command-line entry point: fooble <subcommand>."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fooble")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("crawl", help="fetch recipe pages into the HTML cache")
    p.add_argument("--limit", type=int)
    p.add_argument("--refresh", action="store_true", help="refetch cached pages too")
    p.add_argument("--id", type=int, action="append", dest="ids", help="only these recipe ids")

    p = sub.add_parser("extract", help="parse cached HTML into recipe JSON")
    p.add_argument("--id", type=int, action="append", dest="ids")

    sub.add_parser("index", help="build the SQLite index from recipe JSON")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.cmd == "crawl":
        from .crawl import crawl

        n = 0
        for rid, status in crawl(args.data_dir, args.limit, args.refresh, args.ids):
            n += 1
            print(f"{rid}\t{status}", flush=True)
        print(f"fetched {n}", file=sys.stderr)
    if args.cmd == "extract":
        from .extract import extract_all

        ok, failed = extract_all(args.data_dir, args.ids)
        print(f"extracted {ok}, failed {failed}", file=sys.stderr)
        return 1 if failed else 0
    if args.cmd == "index":
        from .index import build_index

        n = build_index(args.data_dir)
        print(f"indexed {n} recipes", file=sys.stderr)
    return 0
