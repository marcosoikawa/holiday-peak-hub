"""Export product data from CRUD service (or mock seed data) to CSV and JSON.

This script supports the hot-demo pipeline by producing the initial CSV/JSON
artifacts that feed into blob upload → enrichment → AI Search indexing.

Usage:
    # Live mode (requires running CRUD service):
    python scripts/demo/export_products_csv.py

    # Mock mode (uses embedded seed data, no running service needed):
    python scripts/demo/export_products_csv.py --mock

    # Override CRUD URL and output directory:
    python scripts/demo/export_products_csv.py --crud-url http://crud:8000 --output-dir ./out
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CSV_COLUMNS: list[str] = [
    "entity_id",
    "sku",
    "name",
    "description",
    "brand",
    "category",
    "price",
    "currency",
    "image_url",
    "features",
    "rating",
    "tags",
]

DEFAULT_CRUD_URL = "http://localhost:8000"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "demos" / "sample-data"
PIPE_DELIMITER = "|"


# ---------------------------------------------------------------------------
# Mock data generation
# ---------------------------------------------------------------------------


def _build_mock_products() -> list[dict[str, Any]]:
    """Build product dicts from the embedded seed catalog in ``seed_demo_data``."""
    # Import lazily so --mock works without DB dependencies being installed.
    seed_path = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "crud-service"
        / "src"
        / "crud_service"
        / "scripts"
    )
    if str(seed_path) not in sys.path:
        sys.path.insert(0, str(seed_path))

    from seed_demo_data import CATEGORIES, _PRODUCTS_BY_CATEGORY  # type: ignore[import-untyped]

    category_map: dict[str, str] = {cat["id"]: cat["name"] for cat in CATEGORIES}

    products: list[dict[str, Any]] = []
    for category_id, items in _PRODUCTS_BY_CATEGORY.items():
        category_name = category_map.get(category_id, category_id)
        for name, description, price, features, image_keyword in items:
            product_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{category_id}/{name}"))
            products.append(
                {
                    "id": product_id,
                    "name": name,
                    "description": description,
                    "price": price,
                    "category_id": category_id,
                    "category_name": category_name,
                    "image_url": f"https://images.unsplash.com/photo-{image_keyword}?w=800&q=80",
                    "features": features,
                    "rating": None,
                    "tags": features,
                    "in_stock": True,
                }
            )
    return products


# ---------------------------------------------------------------------------
# Live fetch
# ---------------------------------------------------------------------------


def _fetch_products_live(crud_url: str) -> list[dict[str, Any]]:
    """Fetch products from the running CRUD service via ``httpx``."""
    import httpx  # noqa: PLC0415

    url = f"{crud_url.rstrip('/')}/api/products"
    response = httpx.get(url, params={"limit": 200}, timeout=30.0)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _resolve_category(product: dict[str, Any]) -> str:
    """Return a human-readable category string."""
    return str(
        product.get("category_name")
        or product.get("category")
        or product.get("category_id")
        or ""
    )


def _join_multi(values: list[str] | None) -> str:
    """Join a list into a pipe-delimited string."""
    if not values:
        return ""
    return PIPE_DELIMITER.join(str(v) for v in values)


def product_to_row(product: dict[str, Any]) -> dict[str, str]:
    """Map a raw product dict to a flat CSV-compatible row."""
    product_id = str(product.get("id", ""))
    return {
        "entity_id": product_id,
        "sku": str(product.get("sku", product_id)),
        "name": str(product.get("name", "")),
        "description": str(product.get("description", "")),
        "brand": str(product.get("brand", "")),
        "category": _resolve_category(product),
        "price": str(product.get("price", "")),
        "currency": str(product.get("currency", "USD")),
        "image_url": str(product.get("image_url", "")),
        "features": _join_multi(product.get("features")),
        "rating": str(product.get("rating", "")),
        "tags": _join_multi(product.get("tags")),
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_csv(products: list[dict[str, Any]], output_path: Path) -> Path:
    """Write products to a CSV file and return the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [product_to_row(p) for p in products]
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def write_json(products: list[dict[str, Any]], output_path: Path) -> Path:
    """Write raw product dicts to a JSON file and return the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(products, fh, indent=2, default=str)
    return output_path


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(products: list[dict[str, Any]], csv_path: Path, json_path: Path) -> None:
    """Print a human-readable export summary to stdout."""
    categories = sorted({_resolve_category(p) for p in products if _resolve_category(p)})
    print(f"\n{'='*60}")
    print("  Product Export Summary")
    print(f"{'='*60}")
    print(f"  Total products exported : {len(products)}")
    print(f"  Categories found ({len(categories)})   : {', '.join(categories)}")
    print(f"  CSV output              : {csv_path}")
    print(f"  JSON output             : {json_path}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Export product catalog to CSV and JSON for the hot-demo pipeline.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=False,
        help="Use embedded seed data instead of calling the CRUD service.",
    )
    parser.add_argument(
        "--crud-url",
        type=str,
        default=None,
        help=f"CRUD service base URL (default: $CRUD_SERVICE_URL or {DEFAULT_CRUD_URL}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Directory for output files (default: {DEFAULT_OUTPUT_DIR}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    args = parse_args(argv)

    # Resolve output directory.
    output_dir: Path = args.output_dir or DEFAULT_OUTPUT_DIR
    csv_path = output_dir / "products_export.csv"
    json_path = output_dir / "products_export.json"

    # Fetch products.
    if args.mock:
        products = _build_mock_products()
    else:
        crud_url = args.crud_url or os.getenv("CRUD_SERVICE_URL", DEFAULT_CRUD_URL)
        products = _fetch_products_live(crud_url)

    if not products:
        print("ERROR: No products returned. Aborting.", file=sys.stderr)
        sys.exit(1)

    # Write outputs.
    write_csv(products, csv_path)
    write_json(products, json_path)

    # Summary.
    print_summary(products, csv_path, json_path)


if __name__ == "__main__":
    main()
