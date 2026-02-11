#!/usr/bin/env python
"""
Recursively import JPEG images into Postgres using landlensdb.

Loads configuration from environment variables and optionally CLI flags, walks a
directory, builds a GeoImageFrame, and upserts rows into the target table.
"""

from __future__ import annotations

import argparse
import sys
import os
import warnings
from pathlib import Path
from typing import Iterable, Tuple

from dotenv import load_dotenv

from landlensdb.handlers.db import Postgres
from landlensdb.handlers.image import Local
from sqlalchemy import text

ALLOWED_EXTENSIONS = (".jpg", ".jpeg")
DEFAULT_THUMBNAIL_SIZE = (256, 256)


def parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_thumbnail_size(raw: str | None) -> Tuple[int, int]:
    if not raw:
        return DEFAULT_THUMBNAIL_SIZE

    normalized = raw.lower().replace("x", ",")
    parts = [p for p in normalized.split(",") if p]
    if len(parts) != 2:
        raise ValueError("Thumbnail size must look like 256x256 or 256,256")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("Thumbnail size values must be integers") from exc


def load_config(args: argparse.Namespace) -> dict:
    load_dotenv()

    database_url = args.database_url or os.getenv("LANDLENS_DATABASE_URL")
    table_name = args.table or os.getenv("LANDLENS_TABLE")
    table_schema = args.schema or os.getenv("LANDLENS_TABLE_SCHEMA") or None
    conflict = args.conflict or os.getenv("LANDLENS_ON_CONFLICT", "update")
    if conflict not in {"update", "nothing"}:
        raise ValueError("LANDLENS_ON_CONFLICT must be 'update' or 'nothing'")

    env_thumbnails = parse_bool(os.getenv("LANDLENS_CREATE_THUMBNAILS"), True)
    create_thumbnails = env_thumbnails and not args.no_thumbnails
    thumb_size = parse_thumbnail_size(
        args.thumbnail_size or os.getenv("LANDLENS_THUMBNAIL_SIZE")
    )
    skip_existing_dirs = parse_bool(
        os.getenv("LANDLENS_SKIP_EXISTING_DIRS"), False
    ) or args.skip_existing_dirs

    missing = []
    if not database_url:
        missing.append("LANDLENS_DATABASE_URL")
    if not table_name:
        missing.append("LANDLENS_TABLE")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    return {
        "database_url": database_url,
        "table_name": table_name,
        "table_schema": table_schema,
        "conflict": conflict,
        "create_thumbnails": create_thumbnails,
        "thumbnail_size": thumb_size,
        "skip_existing_dirs": skip_existing_dirs,
    }


def load_images_filtered(
    root: Path,
    create_thumbnails: bool,
    thumbnail_size: Tuple[int, int],
    skip_dirs: Iterable[Path],
):
    """
    Patch os.walk within landlensdb to filter directories/files and skip known paths.
    """
    from landlensdb.handlers import image as image_module

    skip_set = {p.resolve() for p in skip_dirs}
    original_walk = image_module.os.walk
    original_get_exif = image_module.Local.get_exif_data
    original_image_open = image_module.Image.open

    def safe_get_exif(img):
        if img is None:
            return {}
        try:
            return original_get_exif(img)
        except Exception:
            # If an image lacks EXIF support, return empty so it gets skipped gracefully.
            return {}

    def safe_image_open(path):
        try:
            return original_image_open(path)
        except Exception:
            warnings.warn(f"Skipping unreadable image: {path}")
            return None

    def filtered_walk(top):
        if Path(top).resolve() in skip_set:
            return
        for current_root, dirnames, filenames in original_walk(top):
            # Drop known skip directories and macOS metadata folders.
            dirnames[:] = [
                d
                for d in dirnames
                if (Path(current_root) / d).resolve() not in skip_set
                and d != "__MACOSX"
            ]
            filtered_files = [
                f
                for f in filenames
                if f.lower().endswith(ALLOWED_EXTENSIONS)
            ]
            yield current_root, dirnames, filtered_files

    image_module.Local.get_exif_data = staticmethod(safe_get_exif)
    image_module.Image.open = safe_image_open
    image_module.os.walk = filtered_walk
    try:
        return image_module.Local.load_images(
            directory=str(root),
            create_thumbnails=create_thumbnails,
            thumbnail_size=thumbnail_size,
        )
    finally:
        image_module.os.walk = original_walk
        image_module.Local.get_exif_data = original_get_exif
        image_module.Image.open = original_image_open


def build_geoimageframe(
    root: Path,
    create_thumbnails: bool,
    thumbnail_size: Tuple[int, int],
    skip_dirs: Iterable[Path],
):
    gif = load_images_filtered(
        root=root,
        create_thumbnails=create_thumbnails,
        thumbnail_size=thumbnail_size,
        skip_dirs=skip_dirs,
    )
    lower_urls = gif["image_url"].str.lower()
    filtered = gif[lower_urls.str.endswith(ALLOWED_EXTENSIONS)]
    dropped = len(gif) - len(filtered)

    if filtered.empty:
        raise ValueError(f"No JPEG images found in {root}")

    filtered.reset_index(drop=True, inplace=True)
    return filtered, dropped


def fetch_existing_dirs(db: Postgres, table: str, root: Path, schema: str | None) -> set[Path]:
    root_str = str(root.resolve())
    stmt = text(
        f"""
        select distinct regexp_replace(image_url, '/[^/]+$', '') as dir
        from {f'{schema}.{table}' if schema else table}
        where image_url like :prefix
        """
    )

    with db.engine.connect() as conn:
        result = conn.execute(stmt, {"prefix": f"{root_str}%"})
        dirs = {Path(row[0]).resolve() for row in result if row[0]}
    return dirs


def align_columns_to_table(db: Postgres, table: str, schema: str | None, gif):
    """
    Keep only columns that exist in the target table; warn about dropped ones.
    """
    from sqlalchemy import inspect, Table, MetaData

    inspector = inspect(db.engine)
    table_cols = {col["name"] for col in inspector.get_columns(table, schema=schema)}
    required = {"image_url", "name", "geometry"}
    missing_required = required - table_cols
    if missing_required:
        raise ValueError(
            f"Target table '{table}' is missing required columns: {', '.join(sorted(missing_required))}"
        )

    present_cols = [col for col in gif.columns if col in table_cols]
    dropped_cols = [col for col in gif.columns if col not in table_cols]
    if dropped_cols:
        warnings.warn(
            f"Dropping columns not present in '{table}': {', '.join(dropped_cols)}"
        )
    # ensure table is loaded with schema for upsert
    meta = MetaData()
    Table(table, meta, schema=schema, autoload_with=db.engine)
    return gif[present_cols]


def import_images(args: argparse.Namespace) -> None:
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Directory not found: {root}")

    config = load_config(args)
    db = Postgres(config["database_url"])

    skip_dirs: set[Path] = set()
    if config["skip_existing_dirs"]:
        print("Fetching existing image directories from database...")
        skip_dirs = fetch_existing_dirs(db, config["table_name"], root, config["table_schema"])
        print(f"Will skip {len(skip_dirs)} directories already in the database.")

    print(f"Scanning {root} for JPEG images...")
    gif, dropped = build_geoimageframe(
        root,
        config["create_thumbnails"],
        config["thumbnail_size"],
        skip_dirs,
    )

    if dropped:
        print(f"Skipped {dropped} non-JPEG images that were discovered during scanning.")

    gif = align_columns_to_table(db, config["table_name"], config["table_schema"], gif)

    print(f"Table columns: {list(gif.columns)}")
    print(f"Found {len(gif)} JPEG images; importing into {config['table_name']}...")
    try:
        db.upsert_images(gif, config["table_name"], conflict=config["conflict"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Upsert failed for table '{config['table_name']}' (schema={config['table_schema']}). "
            f"Columns being sent: {list(gif.columns)}. Error: {exc}"
        ) from exc
    print("Import complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recursively import JPEG images into Postgres using landlensdb."
    )
    parser.add_argument(
        "directory",
        help="Root directory containing JPEG images to import.",
    )
    parser.add_argument(
        "--table",
        dest="table",
        help="Override target table name (defaults to LANDLENS_TABLE).",
    )
    parser.add_argument(
        "--database-url",
        dest="database_url",
        help="Override database URL (defaults to LANDLENS_DATABASE_URL).",
    )
    parser.add_argument(
        "--schema",
        dest="schema",
        help="Table schema if not on the default search_path (defaults to LANDLENS_TABLE_SCHEMA).",
    )
    parser.add_argument(
        "--conflict",
        choices=["update", "nothing"],
        help="Upsert behavior; overrides LANDLENS_ON_CONFLICT.",
    )
    parser.add_argument(
        "--no-thumbnails",
        action="store_true",
        help="Disable thumbnail generation regardless of the env flag.",
    )
    parser.add_argument(
        "--thumbnail-size",
        help="Override thumbnail size, e.g., 256x256 (defaults to LANDLENS_THUMBNAIL_SIZE).",
    )
    parser.add_argument(
        "--skip-existing-dirs",
        action="store_true",
        help="Skip descending into directories already present in the database (based on image_url).",
    )
    return parser.parse_args()


def main() -> None:
    try:
        import_images(parse_args())
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
