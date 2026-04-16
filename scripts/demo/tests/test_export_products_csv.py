"""Tests for the demo product export script."""

from __future__ import annotations

import csv
import json
import io
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parents[1]

# Ensure the script directory is importable.
import sys

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from export_products_csv import (
    CSV_COLUMNS,
    PIPE_DELIMITER,
    _build_mock_products,
    product_to_row,
    write_csv,
    write_json,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_products() -> list[dict]:
    """Generate mock products once for the entire test module."""
    return _build_mock_products()


@pytest.fixture()
def export_dir(tmp_path: Path) -> Path:
    """Return a temporary output directory."""
    return tmp_path / "export"


# ---------------------------------------------------------------------------
# Test: mock data generation
# ---------------------------------------------------------------------------


class TestMockDataGeneration:
    """Validate mock product data from embedded seed catalog."""

    def test_produces_at_least_100_products(self, mock_products: list[dict]) -> None:
        assert len(mock_products) >= 100, (
            f"Expected >= 100 products, got {len(mock_products)}"
        )

    def test_products_have_required_keys(self, mock_products: list[dict]) -> None:
        required = {"id", "name", "description", "price", "category_id", "features"}
        for product in mock_products:
            missing = required - product.keys()
            assert not missing, f"Product {product.get('name')} missing keys: {missing}"

    def test_product_ids_are_valid_uuids(self, mock_products: list[dict]) -> None:
        import uuid as _uuid

        for product in mock_products:
            _uuid.UUID(product["id"])  # raises ValueError on invalid UUID

    def test_product_ids_are_deterministic(self, mock_products: list[dict]) -> None:
        """uuid5 with the same seed must produce the same IDs on re-run."""
        second_run = _build_mock_products()
        ids_first = [p["id"] for p in mock_products]
        ids_second = [p["id"] for p in second_run]
        assert ids_first == ids_second

    def test_all_11_categories_present(self, mock_products: list[dict]) -> None:
        categories = {p.get("category_name") for p in mock_products}
        assert len(categories) == 11, f"Expected 11 categories, got {len(categories)}: {categories}"

    def test_prices_are_positive(self, mock_products: list[dict]) -> None:
        for product in mock_products:
            assert product["price"] > 0, f"Invalid price for {product['name']}"


# ---------------------------------------------------------------------------
# Test: row mapping
# ---------------------------------------------------------------------------


class TestProductToRow:
    """Validate the flat-row mapping function."""

    def test_row_has_all_csv_columns(self, mock_products: list[dict]) -> None:
        row = product_to_row(mock_products[0])
        assert set(row.keys()) == set(CSV_COLUMNS)

    def test_features_are_pipe_delimited(self, mock_products: list[dict]) -> None:
        product = next(p for p in mock_products if p.get("features"))
        row = product_to_row(product)
        assert PIPE_DELIMITER in row["features"]
        parts = row["features"].split(PIPE_DELIMITER)
        assert len(parts) == len(product["features"])

    def test_tags_are_pipe_delimited(self, mock_products: list[dict]) -> None:
        product = next(p for p in mock_products if p.get("tags"))
        row = product_to_row(product)
        assert PIPE_DELIMITER in row["tags"]

    def test_entity_id_matches_product_id(self, mock_products: list[dict]) -> None:
        product = mock_products[0]
        row = product_to_row(product)
        assert row["entity_id"] == product["id"]

    def test_empty_features_produces_empty_string(self) -> None:
        product: dict = {"id": "x", "name": "x", "features": None, "tags": None}
        row = product_to_row(product)
        assert row["features"] == ""
        assert row["tags"] == ""


# ---------------------------------------------------------------------------
# Test: CSV output
# ---------------------------------------------------------------------------


class TestCSVOutput:
    """Validate CSV file structure and content."""

    def test_csv_has_correct_headers(
        self, mock_products: list[dict], export_dir: Path
    ) -> None:
        csv_path = write_csv(mock_products, export_dir / "products.csv")
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames is not None
            assert list(reader.fieldnames) == CSV_COLUMNS

    def test_csv_row_count_matches(
        self, mock_products: list[dict], export_dir: Path
    ) -> None:
        csv_path = write_csv(mock_products, export_dir / "products.csv")
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        assert len(rows) == len(mock_products)

    def test_csv_uses_quote_all(
        self, mock_products: list[dict], export_dir: Path
    ) -> None:
        """Ensure every field is quoted for Excel/pandas compatibility."""
        csv_path = write_csv(mock_products, export_dir / "products.csv")
        raw = csv_path.read_text(encoding="utf-8")
        header_line = raw.splitlines()[0]
        # All column names must be double-quoted.
        for col in CSV_COLUMNS:
            assert f'"{col}"' in header_line

    def test_csv_loadable_with_csv_reader(
        self, mock_products: list[dict], export_dir: Path
    ) -> None:
        csv_path = write_csv(mock_products, export_dir / "products.csv")
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            assert header == CSV_COLUMNS
            data_rows = list(reader)
        assert len(data_rows) == len(mock_products)

    def test_csv_idempotent_overwrite(
        self, mock_products: list[dict], export_dir: Path
    ) -> None:
        csv_path = export_dir / "products.csv"
        write_csv(mock_products, csv_path)
        first_content = csv_path.read_text(encoding="utf-8")
        write_csv(mock_products, csv_path)
        second_content = csv_path.read_text(encoding="utf-8")
        assert first_content == second_content


# ---------------------------------------------------------------------------
# Test: JSON output
# ---------------------------------------------------------------------------


class TestJSONOutput:
    """Validate JSON file structure and CSV/JSON consistency."""

    def test_json_is_valid_array(
        self, mock_products: list[dict], export_dir: Path
    ) -> None:
        json_path = write_json(mock_products, export_dir / "products.json")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == len(mock_products)

    def test_json_matches_csv_entity_ids(
        self, mock_products: list[dict], export_dir: Path
    ) -> None:
        csv_path = write_csv(mock_products, export_dir / "products.csv")
        json_path = write_json(mock_products, export_dir / "products.json")

        with csv_path.open(newline="", encoding="utf-8") as fh:
            csv_ids = {row["entity_id"] for row in csv.DictReader(fh)}

        json_data = json.loads(json_path.read_text(encoding="utf-8"))
        json_ids = {str(p["id"]) for p in json_data}

        assert csv_ids == json_ids

    def test_json_product_has_features_as_list(
        self, mock_products: list[dict], export_dir: Path
    ) -> None:
        json_path = write_json(mock_products, export_dir / "products.json")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        product_with_features = next(p for p in data if p.get("features"))
        assert isinstance(product_with_features["features"], list)
