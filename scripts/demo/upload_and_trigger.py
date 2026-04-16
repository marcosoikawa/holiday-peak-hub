"""Upload product CSV to Azure Blob Storage and trigger enrichment via Event Hub.

This script supports the hot-demo pipeline by uploading individual product JSON
blobs and publishing enrichment job events for each product.

Usage:
    # Full pipeline (upload + events):
    python scripts/demo/upload_and_trigger.py

    # Custom CSV path:
    python scripts/demo/upload_and_trigger.py --csv-path ./my_products.csv

    # Dry-run (no side effects):
    python scripts/demo/upload_and_trigger.py --dry-run

    # Upload only (skip Event Hub publishing):
    python scripts/demo/upload_and_trigger.py --skip-events

    # Control upload concurrency:
    python scripts/demo/upload_and_trigger.py --batch-size 5
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CSV_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "demos"
    / "sample-data"
    / "products_export.csv"
)
DEFAULT_BLOB_CONTAINER = "raw_data"
DEFAULT_BATCH_SIZE = 10
PIPE_DELIMITER = "|"

BLOB_ACCOUNT_URL_ENV = "BLOB_ACCOUNT_URL"
BLOB_CONTAINER_ENV = "TRUTH_PRODUCT_BLOB_CONTAINER"


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def parse_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Parse a product CSV file into a list of product dicts.

    Pipe-delimited fields (``features``, ``tags``) are split back into lists.
    Numeric fields (``price``, ``rating``) are converted to their native types.
    """
    products: list[dict[str, Any]] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            product = dict(row)
            # Split pipe-delimited fields into lists
            for field in ("features", "tags"):
                raw = product.get(field, "")
                if raw and raw != "None":
                    product[field] = [v.strip() for v in raw.split(PIPE_DELIMITER) if v.strip()]
                else:
                    product[field] = []

            # Convert numeric fields
            try:
                product["price"] = float(product["price"])
            except (ValueError, TypeError, KeyError):
                pass
            raw_rating = product.get("rating")
            if raw_rating and raw_rating != "None":
                try:
                    product["rating"] = float(raw_rating)
                except (ValueError, TypeError):
                    product["rating"] = None
            else:
                product["rating"] = None

            products.append(product)
    return products


# ---------------------------------------------------------------------------
# Blob upload
# ---------------------------------------------------------------------------


async def upload_blobs(
    products: list[dict[str, Any]],
    blob_account_url: str,
    container_name: str,
    batch_size: int,
    *,
    dry_run: bool = False,
) -> int:
    """Upload each product as an individual JSON blob.

    Returns the number of successfully uploaded blobs.
    """
    if dry_run:
        for i, product in enumerate(products, 1):
            entity_id = product["entity_id"]
            print(f"[DRY-RUN] Would upload blob: {entity_id}.json")
            if i % 10 == 0:
                print(f"  ... {i}/{len(products)} products processed")
        return len(products)

    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobServiceClient

    uploaded = 0
    semaphore = asyncio.Semaphore(batch_size)
    credential = DefaultAzureCredential()

    try:
        async with BlobServiceClient(
            account_url=blob_account_url,
            credential=credential,
        ) as blob_service:
            container_client = blob_service.get_container_client(container_name)

            async def _upload_one(product: dict[str, Any]) -> bool:
                entity_id = product["entity_id"]
                blob_name = f"{entity_id}.json"
                blob_data = json.dumps(product, ensure_ascii=False, indent=2)
                async with semaphore:
                    try:
                        await container_client.upload_blob(
                            name=blob_name,
                            data=blob_data,
                            overwrite=True,
                        )
                        return True
                    except Exception as exc:
                        print(f"[ERROR] Failed to upload {blob_name}: {exc}")
                        return False

            tasks = [_upload_one(p) for p in products]
            results = await asyncio.gather(*tasks)

            for i, success in enumerate(results, 1):
                if success:
                    uploaded += 1
                if i % 10 == 0:
                    print(f"  ... {i}/{len(products)} uploads completed")
    finally:
        await credential.close()

    return uploaded


# ---------------------------------------------------------------------------
# Event Hub publishing
# ---------------------------------------------------------------------------


async def publish_events(
    products: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> int:
    """Publish enrichment-job events for each product.

    Returns the number of events successfully published.
    """
    if dry_run:
        for product in products:
            entity_id = product["entity_id"]
            print(f"[DRY-RUN] Would publish event for: {entity_id}")
        return len(products)

    from holiday_peak_lib.utils.truth_event_hub import (
        ENRICHMENT_JOBS_TOPIC,
        build_truth_event_publisher_from_env,
    )

    publisher = build_truth_event_publisher_from_env(
        service_name="demo-upload-trigger",
        namespace_env="PLATFORM_JOBS_EVENT_HUB_NAMESPACE",
        connection_string_env="PLATFORM_JOBS_EVENT_HUB_CONNECTION_STRING",
    )

    published = 0
    for i, product in enumerate(products, 1):
        entity_id = product["entity_id"]
        payload: dict[str, Any] = {
            "event_type": "product.uploaded",
            "data": {
                "entity_id": entity_id,
                "source": "csv_upload",
                "product_name": product.get("name", ""),
                "category": product.get("category", ""),
            },
        }
        try:
            await publisher.publish_payload(
                ENRICHMENT_JOBS_TOPIC,
                payload,
                metadata={"domain": "demo-upload", "entity_id": entity_id},
            )
            published += 1
        except Exception as exc:
            print(f"[ERROR] Failed to publish event for {entity_id}: {exc}")

        if i % 10 == 0:
            print(f"  ... {i}/{len(products)} events published")

    return published


# ---------------------------------------------------------------------------
# CLI & main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Upload product CSV to Blob Storage and trigger enrichment pipeline.",
    )
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help="Path to the product CSV file (default: docs/demos/sample-data/products_export.csv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without executing blob uploads or event publishing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Upload concurrency limit (default: 10)",
    )
    parser.add_argument(
        "--skip-events",
        action="store_true",
        help="Upload blobs but skip Event Hub publishing",
    )
    return parser


async def async_main(args: argparse.Namespace) -> None:
    """Async entry point."""
    csv_path: Path = args.csv_path
    dry_run: bool = args.dry_run
    batch_size: int = args.batch_size
    skip_events: bool = args.skip_events

    blob_account_url = os.getenv(BLOB_ACCOUNT_URL_ENV, "")
    container_name = os.getenv(BLOB_CONTAINER_ENV, DEFAULT_BLOB_CONTAINER)

    # --- Validate environment ---
    if not blob_account_url and not dry_run:
        print(
            f"ERROR: {BLOB_ACCOUNT_URL_ENV} environment variable is required "
            "(use --dry-run to skip actual uploads)."
        )
        sys.exit(1)

    if not skip_events and not dry_run:
        ns = os.getenv("PLATFORM_JOBS_EVENT_HUB_NAMESPACE", "")
        cs = os.getenv("PLATFORM_JOBS_EVENT_HUB_CONNECTION_STRING", "")
        if not ns and not cs:
            print(
                "WARNING: Neither PLATFORM_JOBS_EVENT_HUB_NAMESPACE nor "
                "PLATFORM_JOBS_EVENT_HUB_CONNECTION_STRING is set. "
                "Event publishing may fail."
            )

    # --- Parse CSV ---
    if not csv_path.exists():
        print(f"ERROR: CSV file not found: {csv_path}")
        sys.exit(1)

    products = parse_csv(csv_path)
    print(f"Parsed {len(products)} products from {csv_path.name}")

    start = time.monotonic()

    # --- Upload blobs ---
    print(f"\nUploading blobs to container '{container_name}'...")
    uploaded = await upload_blobs(
        products,
        blob_account_url,
        container_name,
        batch_size,
        dry_run=dry_run,
    )
    print(f"Blob upload complete: {uploaded}/{len(products)} succeeded")

    # --- Publish events ---
    published = 0
    if skip_events:
        print("\nSkipping Event Hub publishing (--skip-events)")
    else:
        print("\nPublishing enrichment events...")
        published = await publish_events(products, dry_run=dry_run)
        print(f"Event publishing complete: {published}/{len(products)} succeeded")

    elapsed = time.monotonic() - start

    # --- Summary ---
    print("\n--- Summary ---")
    print(f"  Products uploaded : {uploaded}")
    print(f"  Events published  : {published}")
    print(f"  Container         : {container_name}")
    print(f"  Elapsed time      : {elapsed:.2f}s")


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
