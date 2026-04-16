"""Tests for the demo upload-and-trigger script."""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parents[1]

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from upload_and_trigger import (
    BLOB_ACCOUNT_URL_ENV,
    BLOB_CONTAINER_ENV,
    DEFAULT_BLOB_CONTAINER,
    DEFAULT_CSV_PATH,
    PIPE_DELIMITER,
    async_main,
    build_parser,
    parse_csv,
    publish_events,
    upload_blobs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CSV_CONTENT = (
    '"entity_id","sku","name","description","brand","category","price","currency",'
    '"image_url","features","rating","tags"\n'
    '"aaa-111","aaa-111","Widget A","Desc A","BrandX","Electronics","19.99","USD",'
    '"https://img/a","Feat1|Feat2","4.5","tag1|tag2"\n'
    '"bbb-222","bbb-222","Widget B","Desc B","BrandY","Home","9.99","USD",'
    '"https://img/b","FeatX","None","tagA|tagB|tagC"\n'
    '"ccc-333","ccc-333","Widget C","Desc C","","Toys","5.50","USD",'
    '"https://img/c","","3.0",""\n'
)


@pytest.fixture()
def csv_file(tmp_path: Path) -> Path:
    """Write a small CSV file and return its path."""
    csv_path = tmp_path / "products.csv"
    csv_path.write_text(SAMPLE_CSV_CONTENT, encoding="utf-8")
    return csv_path


@pytest.fixture()
def products(csv_file: Path) -> list[dict[str, Any]]:
    """Parse the sample CSV into product dicts."""
    return parse_csv(csv_file)


# ---------------------------------------------------------------------------
# Tests: CSV parsing
# ---------------------------------------------------------------------------


class TestCSVParsing:
    """Validate CSV → product dict conversion."""

    def test_parses_correct_count(self, products: list[dict[str, Any]]) -> None:
        assert len(products) == 3

    def test_entity_id_preserved(self, products: list[dict[str, Any]]) -> None:
        ids = [p["entity_id"] for p in products]
        assert ids == ["aaa-111", "bbb-222", "ccc-333"]

    def test_features_split_to_list(self, products: list[dict[str, Any]]) -> None:
        assert products[0]["features"] == ["Feat1", "Feat2"]
        assert products[1]["features"] == ["FeatX"]

    def test_empty_features_become_empty_list(self, products: list[dict[str, Any]]) -> None:
        assert products[2]["features"] == []

    def test_tags_split_to_list(self, products: list[dict[str, Any]]) -> None:
        assert products[0]["tags"] == ["tag1", "tag2"]
        assert products[1]["tags"] == ["tagA", "tagB", "tagC"]

    def test_empty_tags_become_empty_list(self, products: list[dict[str, Any]]) -> None:
        assert products[2]["tags"] == []

    def test_price_is_float(self, products: list[dict[str, Any]]) -> None:
        assert products[0]["price"] == 19.99
        assert isinstance(products[0]["price"], float)

    def test_rating_is_float_or_none(self, products: list[dict[str, Any]]) -> None:
        assert products[0]["rating"] == 4.5
        assert products[1]["rating"] is None  # "None" string → None
        assert products[2]["rating"] == 3.0

    def test_string_fields_intact(self, products: list[dict[str, Any]]) -> None:
        assert products[0]["name"] == "Widget A"
        assert products[0]["category"] == "Electronics"
        assert products[0]["currency"] == "USD"


# ---------------------------------------------------------------------------
# Tests: Dry-run mode
# ---------------------------------------------------------------------------


class TestDryRun:
    """Dry-run should log but never call blob/event APIs."""

    @pytest.mark.asyncio()
    async def test_dry_run_upload_returns_full_count(
        self, products: list[dict[str, Any]]
    ) -> None:
        count = await upload_blobs(
            products, "https://fake.blob.core.windows.net", "test", 5, dry_run=True
        )
        assert count == len(products)

    @pytest.mark.asyncio()
    async def test_dry_run_publish_returns_full_count(
        self, products: list[dict[str, Any]]
    ) -> None:
        count = await publish_events(products, dry_run=True)
        assert count == len(products)

    @pytest.mark.asyncio()
    async def test_dry_run_does_not_import_azure_sdk(
        self, products: list[dict[str, Any]]
    ) -> None:
        """Dry-run should not attempt to import azure.storage.blob.aio."""
        with patch.dict("sys.modules", {"azure.storage.blob.aio": None}):
            count = await upload_blobs(
                products, "", "c", 5, dry_run=True
            )
            assert count == len(products)


# ---------------------------------------------------------------------------
# Tests: Blob upload (mocked)
# ---------------------------------------------------------------------------


class TestBlobUpload:
    """Blob upload with mocked BlobServiceClient."""

    @pytest.mark.asyncio()
    async def test_uploads_each_product_as_json_blob(
        self, products: list[dict[str, Any]]
    ) -> None:
        mock_container = MagicMock()
        mock_container.upload_blob = AsyncMock()

        mock_service = AsyncMock()
        mock_service.get_container_client = MagicMock(return_value=mock_container)
        mock_service.__aenter__ = AsyncMock(return_value=mock_service)
        mock_service.__aexit__ = AsyncMock(return_value=False)

        mock_credential = AsyncMock()
        mock_credential.close = AsyncMock()

        with (
            patch(
                "azure.identity.aio.DefaultAzureCredential",
                return_value=mock_credential,
            ),
            patch(
                "azure.storage.blob.aio.BlobServiceClient",
                return_value=mock_service,
            ),
        ):
            count = await upload_blobs(
                products,
                "https://fake.blob.core.windows.net",
                "test-container",
                5,
                dry_run=False,
            )

        assert count == len(products)
        assert mock_container.upload_blob.call_count == len(products)

        # Verify blob names
        blob_names = [
            call.kwargs["name"] if "name" in call.kwargs else call.args[0]
            for call in mock_container.upload_blob.call_args_list
        ]
        for product in products:
            assert f"{product['entity_id']}.json" in blob_names

    @pytest.mark.asyncio()
    async def test_upload_failure_does_not_halt_batch(
        self, products: list[dict[str, Any]]
    ) -> None:
        call_count = 0

        async def _upload_side_effect(**kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated blob failure")

        mock_container = MagicMock()
        mock_container.upload_blob = AsyncMock(side_effect=_upload_side_effect)

        mock_service = AsyncMock()
        mock_service.get_container_client = MagicMock(return_value=mock_container)
        mock_service.__aenter__ = AsyncMock(return_value=mock_service)
        mock_service.__aexit__ = AsyncMock(return_value=False)

        mock_credential = AsyncMock()
        mock_credential.close = AsyncMock()

        with (
            patch(
                "azure.identity.aio.DefaultAzureCredential",
                return_value=mock_credential,
            ),
            patch(
                "azure.storage.blob.aio.BlobServiceClient",
                return_value=mock_service,
            ),
        ):
            count = await upload_blobs(
                products,
                "https://fake.blob.core.windows.net",
                "test-container",
                5,
                dry_run=False,
            )

        # One failed, rest succeeded
        assert count == len(products) - 1
        assert mock_container.upload_blob.call_count == len(products)


# ---------------------------------------------------------------------------
# Tests: Event publishing (mocked)
# ---------------------------------------------------------------------------


class TestEventPublishing:
    """Event Hub publishing with mocked TruthEventPublisher."""

    @pytest.mark.asyncio()
    async def test_publishes_correct_payload_structure(
        self, products: list[dict[str, Any]]
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_payload = AsyncMock()

        with patch(
            "holiday_peak_lib.utils.truth_event_hub.build_truth_event_publisher_from_env",
            return_value=mock_publisher,
        ):
            count = await publish_events(products, dry_run=False)

        assert count == len(products)
        assert mock_publisher.publish_payload.call_count == len(products)

        # Verify payload structure for the first call
        first_call = mock_publisher.publish_payload.call_args_list[0]
        topic = first_call.args[0]
        payload = first_call.args[1]

        assert topic == "enrichment-jobs"
        assert payload["event_type"] == "product.uploaded"
        assert payload["data"]["entity_id"] == products[0]["entity_id"]
        assert payload["data"]["source"] == "csv_upload"
        assert payload["data"]["product_name"] == products[0]["name"]
        assert payload["data"]["category"] == products[0]["category"]

    @pytest.mark.asyncio()
    async def test_publishes_with_correct_metadata(
        self, products: list[dict[str, Any]]
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_payload = AsyncMock()

        with patch(
            "holiday_peak_lib.utils.truth_event_hub.build_truth_event_publisher_from_env",
            return_value=mock_publisher,
        ):
            await publish_events(products, dry_run=False)

        first_call = mock_publisher.publish_payload.call_args_list[0]
        metadata = first_call.kwargs["metadata"]
        assert metadata["domain"] == "demo-upload"
        assert metadata["entity_id"] == products[0]["entity_id"]

    @pytest.mark.asyncio()
    async def test_publish_failure_continues_batch(
        self, products: list[dict[str, Any]]
    ) -> None:
        call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated Event Hub failure")

        mock_publisher = AsyncMock()
        mock_publisher.publish_payload = AsyncMock(side_effect=_side_effect)

        with patch(
            "holiday_peak_lib.utils.truth_event_hub.build_truth_event_publisher_from_env",
            return_value=mock_publisher,
        ):
            count = await publish_events(products, dry_run=False)

        assert count == len(products) - 1


# ---------------------------------------------------------------------------
# Tests: Environment validation
# ---------------------------------------------------------------------------


class TestEnvironmentValidation:
    """Validate error handling for missing environment variables."""

    @pytest.mark.asyncio()
    async def test_missing_blob_url_exits_without_dry_run(
        self, csv_file: Path
    ) -> None:
        parser = build_parser()
        args = parser.parse_args(["--csv-path", str(csv_file)])

        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            await async_main(args)

        assert exc_info.value.code == 1

    @pytest.mark.asyncio()
    async def test_missing_blob_url_allowed_in_dry_run(
        self, csv_file: Path
    ) -> None:
        parser = build_parser()
        args = parser.parse_args(["--csv-path", str(csv_file), "--dry-run"])

        with patch.dict("os.environ", {}, clear=True):
            await async_main(args)  # Should not raise


# ---------------------------------------------------------------------------
# Tests: CLI argument parsing
# ---------------------------------------------------------------------------


class TestCLIParsing:
    """Validate argparse defaults and overrides."""

    def test_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.csv_path == DEFAULT_CSV_PATH
        assert args.dry_run is False
        assert args.batch_size == 10
        assert args.skip_events is False

    def test_custom_csv_path(self, tmp_path: Path) -> None:
        parser = build_parser()
        args = parser.parse_args(["--csv-path", str(tmp_path / "custom.csv")])
        assert args.csv_path == tmp_path / "custom.csv"

    def test_dry_run_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_skip_events_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--skip-events"])
        assert args.skip_events is True

    def test_batch_size_override(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--batch-size", "25"])
        assert args.batch_size == 25
