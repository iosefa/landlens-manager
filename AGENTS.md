# Repository Guidelines

## Project Structure & Module Organization
- `scripts/import_images.py`: CLI to recurse through a folder, parse EXIF GPS from `.jpg/.jpeg`, and upsert rows with `landlensdb`.
- `.env`: Runtime configuration for database URL, target table, conflict mode, and thumbnail settings; keep secrets out of version control.
- `environment.yml`: Conda environment with Python, GDAL, `landlensdb`, and `python-dotenv`.
- `README.md`: Usage notes and table expectations; update alongside behavior changes.

## Build, Test, and Development Commands
- Create the environment: `conda env create -f environment.yml && conda activate landlens-manager`.
- Set config: copy `.env` and fill `LANDLENS_DATABASE_URL` and `LANDLENS_TABLE`.
- Run the importer: `python scripts/import_images.py /abs/path/to/images --conflict update`.
- Smoke-test imports by pointing at a small sample folder before large batches.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation; prefer type hints on new functions and helpers.
- Use `pathlib.Path` for file paths and keep configuration keys uppercase with the `LANDLENS_` prefix.
- Keep CLI flags short and self-describing; default behaviors should mirror environment variables.
- Add concise docstrings and inline comments only where behavior is non-obvious (e.g., filtering logic or DB expectations).

## Testing Guidelines
- No test suite exists yet; add targeted `pytest` cases when extending logic (e.g., JPEG filtering, thumbnail parsing, conflict handling).
- Use temporary directories and fake EXIF data where possible to avoid hitting real databases in unit tests.
- For manual checks, run against a local Postgres instance with a disposable table and confirm geometry persists as EPSG:4326 Points.

## Commit & Pull Request Guidelines
- Use clear, imperative commit messages (e.g., `add jpeg importer cli`, `document db table expectations`).
- PRs should state scope, include usage notes or sample commands, and link related issues if present.
- If setup or config changes, update `README.md`, `AGENTS.md`, and `.env` defaults in the same PR.
- Include before/after notes for behavior changes (e.g., conflict mode defaults, thumbnail handling).

## Security & Configuration Tips
- Never commit real `.env` values; share connection strings through secure channels only.
- Ensure the target table has a unique constraint on `image_url` (`<table>_image_url_key`) to match `landlensdb` upserts.
- Keep GDAL and `landlensdb` aligned with `environment.yml`; regenerate the lock only when upgrading dependencies intentionally.
- Use `--skip-existing-dirs` or `LANDLENS_SKIP_EXISTING_DIRS=true` to avoid re-walking directories already present in the database when ingesting from large mounts.
