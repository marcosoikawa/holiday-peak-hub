"""Tests for the demo search-validation script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parents[1]

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_search import (
    DEFAULT_CATALOG_URL,
    DEFAULT_TIMEOUT,
    DEMO_QUERIES,
    REQUIRED_QUERY_FIELDS,
    QueryResult,
    async_main,
    build_parser,
    count_results,
    evaluate_sla,
    format_results_table,
    print_dry_run,
    run_all_queries,
    run_query,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**overrides: Any) -> argparse.Namespace:
    defaults = {
        "catalog_url": DEFAULT_CATALOG_URL,
        "timeout": DEFAULT_TIMEOUT,
        "dry_run": False,
        "verbose": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _ok_response(result_count: int = 3) -> httpx.Response:
    body = {"results": [{"id": str(i)} for i in range(result_count)]}
    return httpx.Response(200, json=body)


# ---------------------------------------------------------------------------
# Query definitions
# ---------------------------------------------------------------------------


class TestQueryDefinitions:
    """Verify the static DEMO_QUERIES list is well-formed."""

    def test_all_queries_have_required_fields(self) -> None:
        for q in DEMO_QUERIES:
            missing = REQUIRED_QUERY_FIELDS - q.keys()
            assert not missing, f"Query {q.get('name', '?')} missing: {missing}"

    def test_min_results_positive(self) -> None:
        for q in DEMO_QUERIES:
            assert q["min_results"] >= 1, f"{q['name']} has min_results < 1"

    def test_at_least_one_query_defined(self) -> None:
        assert len(DEMO_QUERIES) >= 1


# ---------------------------------------------------------------------------
# SLA evaluation
# ---------------------------------------------------------------------------


class TestEvaluateSla:
    def test_pass_when_under_threshold(self) -> None:
        assert evaluate_sla(4999.0, 5) is True

    def test_fail_when_at_threshold(self) -> None:
        assert evaluate_sla(5000.0, 5) is False

    def test_fail_when_over_threshold(self) -> None:
        assert evaluate_sla(6000.0, 5) is False


# ---------------------------------------------------------------------------
# count_results
# ---------------------------------------------------------------------------


class TestCountResults:
    def test_results_key(self) -> None:
        assert count_results({"results": [1, 2, 3]}) == 3

    def test_products_key(self) -> None:
        assert count_results({"products": [1]}) == 1

    def test_items_key(self) -> None:
        assert count_results({"items": [1, 2]}) == 2

    def test_empty_body(self) -> None:
        assert count_results({}) == 0


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_returns_zero_and_no_http(self) -> None:
        args = _make_args(dry_run=True)
        with patch("validate_search.httpx.AsyncClient") as mock_client_cls:
            exit_code = await async_main(args)
        assert exit_code == 0
        mock_client_cls.assert_not_called()

    def test_dry_run_prints_queries(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_dry_run(DEMO_QUERIES, DEFAULT_CATALOG_URL)
        captured = capsys.readouterr().out
        assert "DRY-RUN" in captured
        for q in DEMO_QUERIES:
            assert q["name"] in captured


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------


class TestFormatResultsTable:
    def test_table_contains_all_query_names(self) -> None:
        results = [
            QueryResult(name="Q1", status=200, result_count=3, time_ms=100.0, sla_pass=True),
            QueryResult(name="Q2", status=500, result_count=0, time_ms=200.0, sla_pass=False),
        ]
        table = format_results_table(results)
        assert "Q1" in table
        assert "Q2" in table

    def test_table_shows_pass_and_fail(self) -> None:
        results = [
            QueryResult(name="A", status=200, result_count=1, time_ms=50.0, sla_pass=True),
            QueryResult(name="B", status=0, result_count=0, time_ms=6000.0, sla_pass=False),
        ]
        table = format_results_table(results)
        assert "PASS" in table
        assert "FAIL" in table

    def test_table_shows_error_string_when_status_zero(self) -> None:
        results = [
            QueryResult(
                name="C", status=0, result_count=0, time_ms=0.0, sla_pass=False, error="timeout"
            ),
        ]
        table = format_results_table(results)
        assert "timeout" in table

    def test_empty_results(self) -> None:
        table = format_results_table([])
        assert "Query" in table  # header still present


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


class TestRunQuery:
    @pytest.mark.asyncio
    async def test_connection_refused(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        result = await run_query(mock_client, DEMO_QUERIES[0], DEFAULT_CATALOG_URL, DEFAULT_TIMEOUT)
        assert result.sla_pass is False
        assert result.error == "connection_refused"
        assert result.status == 0

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.ReadTimeout("timed out")
        result = await run_query(mock_client, DEMO_QUERIES[0], DEFAULT_CATALOG_URL, DEFAULT_TIMEOUT)
        assert result.sla_pass is False
        assert result.error == "timeout"
        assert result.status == 0

    @pytest.mark.asyncio
    async def test_server_500(self) -> None:
        mock_response = httpx.Response(500, json={"error": "internal"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response
        result = await run_query(mock_client, DEMO_QUERIES[0], DEFAULT_CATALOG_URL, DEFAULT_TIMEOUT)
        assert result.sla_pass is False
        assert result.status == 500

    @pytest.mark.asyncio
    async def test_successful_query(self) -> None:
        mock_response = _ok_response(result_count=3)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response
        result = await run_query(mock_client, DEMO_QUERIES[0], DEFAULT_CATALOG_URL, DEFAULT_TIMEOUT)
        assert result.status == 200
        assert result.result_count == 3
        assert result.sla_pass is True


# ---------------------------------------------------------------------------
# Full validation flow (mocked)
# ---------------------------------------------------------------------------


class TestRunAllQueries:
    @pytest.mark.asyncio
    async def test_all_pass(self) -> None:
        mock_response = _ok_response(result_count=2)
        with patch("validate_search.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            results = await run_all_queries(DEFAULT_CATALOG_URL, DEFAULT_TIMEOUT)
        assert len(results) == len(DEMO_QUERIES)
        assert all(r.sla_pass for r in results)

    @pytest.mark.asyncio
    async def test_async_main_returns_1_on_failure(self) -> None:
        mock_response = httpx.Response(500, json={"error": "boom"})
        with patch("validate_search.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            exit_code = await async_main(_make_args())
        assert exit_code == 1


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_defaults(self) -> None:
        args = build_parser().parse_args([])
        assert args.catalog_url == DEFAULT_CATALOG_URL
        assert args.timeout == DEFAULT_TIMEOUT
        assert args.dry_run is False
        assert args.verbose is False

    def test_custom_values(self) -> None:
        args = build_parser().parse_args([
            "--catalog-url", "http://example:9999",
            "--timeout", "10",
            "--dry-run",
            "--verbose",
        ])
        assert args.catalog_url == "http://example:9999"
        assert args.timeout == 10
        assert args.dry_run is True
        assert args.verbose is True
