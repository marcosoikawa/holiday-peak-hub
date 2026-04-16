"""Validate catalog-search service by running demo queries and checking SLA.

This script validates that the end-to-end enrichment pipeline produces
searchable results by executing representative queries against the
ecommerce-catalog-search ``/invoke`` endpoint.

Usage:
    # Run against local catalog-search:
    python scripts/demo/validate_search.py

    # Custom URL and timeout:
    python scripts/demo/validate_search.py --catalog-url http://my-host:8010 --timeout 10

    # Dry-run (no HTTP calls):
    python scripts/demo/validate_search.py --dry-run

    # Verbose output with full payloads:
    python scripts/demo/validate_search.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CATALOG_URL = "http://localhost:8010"
DEFAULT_TIMEOUT = 5

DEMO_QUERIES: list[dict[str, Any]] = [
    {
        "name": "Keyword: winter jacket",
        "query": "warm winter jacket for hiking",
        "mode": "keyword",
        "min_results": 1,
    },
    {
        "name": "Semantic: gift ideas",
        "query": "birthday gift for a tech enthusiast",
        "mode": "vector",
        "min_results": 1,
    },
    {
        "name": "Hybrid: running shoes",
        "query": "lightweight running shoes under $100",
        "mode": "hybrid",
        "min_results": 1,
    },
    {
        "name": "Multi-query: home decor",
        "query": "modern minimalist home decoration ideas",
        "mode": "hybrid",
        "min_results": 1,
    },
]

REQUIRED_QUERY_FIELDS = {"name", "query", "mode", "min_results"}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    """Outcome of a single demo query execution."""

    name: str
    status: int
    result_count: int
    time_ms: float
    sla_pass: bool
    error: str | None = None
    payload: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Validate catalog-search demo queries against /invoke.",
    )
    parser.add_argument(
        "--catalog-url",
        default=DEFAULT_CATALOG_URL,
        help=f"Catalog-search service URL (default: {DEFAULT_CATALOG_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-query timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print queries that would be run without making HTTP calls",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full result payloads",
    )
    return parser


def evaluate_sla(time_ms: float, timeout_seconds: int) -> bool:
    """Return ``True`` when response time is within the SLA threshold."""
    return time_ms < timeout_seconds * 1000


def count_results(response_body: dict[str, Any]) -> int:
    """Extract result count from the invoke response payload."""
    results = response_body.get("results")
    if isinstance(results, list):
        return len(results)
    products = response_body.get("products")
    if isinstance(products, list):
        return len(products)
    items = response_body.get("items")
    if isinstance(items, list):
        return len(items)
    return 0


async def run_query(
    client: httpx.AsyncClient,
    query_def: dict[str, Any],
    catalog_url: str,
    timeout: int,
) -> QueryResult:
    """Execute a single demo query and return the result."""
    url = f"{catalog_url.rstrip('/')}/invoke"
    body: dict[str, Any] = {
        "query": query_def["query"],
        "limit": 5,
        "mode": query_def["mode"],
    }
    start = time.monotonic()
    try:
        response = await client.post(url, json=body, timeout=timeout)
        elapsed_ms = (time.monotonic() - start) * 1000
        response_body = response.json()
        result_count = count_results(response_body)
        sla_pass = (
            response.status_code == 200
            and result_count >= query_def["min_results"]
            and evaluate_sla(elapsed_ms, timeout)
        )
        return QueryResult(
            name=query_def["name"],
            status=response.status_code,
            result_count=result_count,
            time_ms=round(elapsed_ms, 1),
            sla_pass=sla_pass,
            payload=response_body,
        )
    except httpx.TimeoutException:
        elapsed_ms = (time.monotonic() - start) * 1000
        return QueryResult(
            name=query_def["name"],
            status=0,
            result_count=0,
            time_ms=round(elapsed_ms, 1),
            sla_pass=False,
            error="timeout",
        )
    except httpx.ConnectError:
        elapsed_ms = (time.monotonic() - start) * 1000
        return QueryResult(
            name=query_def["name"],
            status=0,
            result_count=0,
            time_ms=round(elapsed_ms, 1),
            sla_pass=False,
            error="connection_refused",
        )


async def run_all_queries(
    catalog_url: str,
    timeout: int,
    queries: list[dict[str, Any]] | None = None,
) -> list[QueryResult]:
    """Run all demo queries sequentially and collect results."""
    queries = queries or DEMO_QUERIES
    results: list[QueryResult] = []
    async with httpx.AsyncClient() as client:
        for q in queries:
            result = await run_query(client, q, catalog_url, timeout)
            results.append(result)
    return results


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

COL_QUERY = 29
COL_STATUS = 8
COL_RESULTS = 10
COL_TIME = 12
COL_SLA = 9


def _pad(text: str, width: int) -> str:
    return text.ljust(width)[:width]


def format_results_table(results: list[QueryResult]) -> str:
    """Render the results as a bordered table string."""
    header = (
        f"│ {_pad('Query', COL_QUERY)} "
        f"│ {_pad('Status', COL_STATUS)} "
        f"│ {_pad('Results', COL_RESULTS)} "
        f"│ {_pad('Time (ms)', COL_TIME)} "
        f"│ {_pad('SLA', COL_SLA)} │"
    )
    sep_top = (
        f"┌─{'─' * COL_QUERY}─"
        f"┬─{'─' * COL_STATUS}─"
        f"┬─{'─' * COL_RESULTS}─"
        f"┬─{'─' * COL_TIME}─"
        f"┬─{'─' * COL_SLA}─┐"
    )
    sep_mid = (
        f"├─{'─' * COL_QUERY}─"
        f"┼─{'─' * COL_STATUS}─"
        f"┼─{'─' * COL_RESULTS}─"
        f"┼─{'─' * COL_TIME}─"
        f"┼─{'─' * COL_SLA}─┤"
    )
    sep_bot = (
        f"└─{'─' * COL_QUERY}─"
        f"┴─{'─' * COL_STATUS}─"
        f"┴─{'─' * COL_RESULTS}─"
        f"┴─{'─' * COL_TIME}─"
        f"┴─{'─' * COL_SLA}─┘"
    )
    rows: list[str] = []
    for r in results:
        status_str = str(r.status) if r.status else r.error or "ERR"
        sla_str = "PASS" if r.sla_pass else "FAIL"
        row = (
            f"│ {_pad(r.name, COL_QUERY)} "
            f"│ {_pad(status_str, COL_STATUS)} "
            f"│ {_pad(str(r.result_count), COL_RESULTS)} "
            f"│ {_pad(str(r.time_ms), COL_TIME)} "
            f"│ {_pad(sla_str, COL_SLA)} │"
        )
        rows.append(row)

    lines = [sep_top, header, sep_mid, *rows, sep_bot]
    return "\n".join(lines)


def print_dry_run(queries: list[dict[str, Any]], catalog_url: str) -> None:
    """Print query definitions without making HTTP calls."""
    print("DRY-RUN: The following queries would be executed:\n")
    for i, q in enumerate(queries, 1):
        body = {"query": q["query"], "limit": 5, "mode": q["mode"]}
        print(f"  [{i}] {q['name']}")
        print(f"      POST {catalog_url.rstrip('/')}/invoke")
        print(f"      Body: {json.dumps(body)}")
        print(f"      Min results: {q['min_results']}")
        print()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def async_main(args: argparse.Namespace | None = None) -> int:
    """Run validation and return exit code (0=all pass, 1=any fail)."""
    if args is None:
        args = build_parser().parse_args()

    queries = DEMO_QUERIES

    if args.dry_run:
        print_dry_run(queries, args.catalog_url)
        return 0

    results = await run_all_queries(args.catalog_url, args.timeout, queries)

    table = format_results_table(results)
    print(table)

    if args.verbose:
        print("\n--- Verbose Payloads ---")
        for r in results:
            print(f"\n[{r.name}]")
            print(json.dumps(r.payload, indent=2, default=str))

    all_pass = all(r.sla_pass for r in results)
    summary = f"\n{'ALL PASS' if all_pass else 'SOME FAILED'}: {sum(r.sla_pass for r in results)}/{len(results)} queries passed SLA."
    print(summary)
    return 0 if all_pass else 1


def main() -> None:
    """CLI entrypoint."""
    exit_code = asyncio.run(async_main())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
