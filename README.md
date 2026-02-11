# landlens-manager

Helper scripts for importing JPEG imagery into Postgres using `landlensdb`.

## Quickstart
- Create the environment: `conda env create -f environment.yml && conda activate landlens-manager`.
- Copy `.env` and update `LANDLENS_DATABASE_URL` and `LANDLENS_TABLE` for your Postgres/PostGIS instance.

## Importing images
```bash
python scripts/import_images.py /path/to/images \
  --table images \                # optional override
  --conflict update \             # or nothing
  --skip-existing-dirs            # skip directories already present in DB
```
- Recurses through the folder, keeps only `.jpg/.jpeg`, and generates thumbnails unless `--no-thumbnails` or `LANDLENS_CREATE_THUMBNAILS=false`.
- Uses EXIF GPS for geometry (EPSG:4326). Files without valid coordinates are skipped.
- Upserts into the target table; defaults to `LANDLENS_ON_CONFLICT=update`.
- `--skip-existing-dirs` (or `LANDLENS_SKIP_EXISTING_DIRS=true`) skips descending into directories already found in the database (based on `image_url` prefix under the root you scan). This assumes new images arrive in brand-new directories, not existing ones.

## Expected database table
- Must already exist with `image_url` (unique), `name`, and `geometry` (Point, EPSG:4326). Additional columns (e.g., `altitude`, `compass_angle`, `camera_type`, `captured_at`, `thumb_url`) should match types produced by `landlensdb.handlers.image.Local`.
- Add a unique constraint named `<table>_image_url_key` on `image_url` to match the library’s upsert.

## Configuration
- `LANDLENS_DATABASE_URL` (required): e.g., `postgresql://postgres:postgres@localhost:5432/images`
- `LANDLENS_TABLE` (required): target table name.
- `LANDLENS_ON_CONFLICT` (optional): `update` (default) or `nothing`.
- `LANDLENS_CREATE_THUMBNAILS` (optional): `true`/`false`.
- `LANDLENS_THUMBNAIL_SIZE` (optional): `256x256` format.
