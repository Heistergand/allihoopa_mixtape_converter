# rename_allihoopa.py

A small command-line utility to rename Allihoopa piece assets (audio, cover, optional attachment) into readable, portable filenames based on the `dump/alltihop.json` metadata dump.

This script was written to convert an Allihoopa mixtape archive (the dump you get from the site) into meaningful filenames like:
`username - Track Title.mp4` (and similarly for cover images and attachments). It also optionally keeps compatibility links with the original names (e.g. `audio.mp4` / `cover.jpg`) and can undo applied renames from a log.

---

## Key behaviour

- Reads metadata from an Allihoopa Mixtape JSON file (default: `<root>/dump/alltihop.json`) and iterates the `pieces` entries.
- For each piece, finds the piece folder (default: `<root>/dump/assets/pieces/<short_id>`).
- Renames up to three asset types:
  - Audio — prefers `audio.mp4`, `audio.m4a`, `audio.wav`, `audio.aac`, or any file whose stem is `audio`.
  - Cover image — prefers `cover.jpg`, `cover.jpeg`, `cover.png`, or any file whose stem is `cover`.
  - Attachment — uses the attachment filename listed in piece metadata (if present).
- Builds a human-readable base name of the form: `username - Title` and appends the original file extension.
- Produces "portable" filenames:
  - Collapses or replaces whitespace (by default: underscores; optional: preserve spaces).
  - Replaces Windows-invalid characters (e.g. `<>:"/\|?*` and control characters) with `_`.
  - Trims trailing dots/spaces and avoids Windows reserved device names (e.g. `CON`, `COM1`).
  - Caps the filename base length to avoid extremely long names.
- Ensures unique destination paths by appending `__N` if necessary.
- Optionally creates a compatibility link at the original name pointing to the new name after renaming:
  - Tries to create a hardlink first; if that fails, tries a relative symlink.
- Writes each performed rename as a JSON object (one per line) to a log file (default: `<root>/rename_log.jsonl`).
- Supports undoing applied operations by reading and reversing the log.

---

## Requirements

- Python 3 (the script uses standard library modules).
- Files and metadata from an Allihoopa dump (the script expects a layout similar to the official dump: `dump/alltihop.json` and `dump/assets/pieces/<short_id>/...`).

---

## Usage

Basic invocation:

- Show what would be done (dry-run — default):
  ```
  python3 rename_allihoopa.py --root /path/to/archive
  ```
  If you don't pass `--apply` or `--undo`, the script runs a dry-run by default and reports planned operations without changing files.

- Apply the renames:
  ```
  python3 rename_allihoopa.py --root /path/to/archive --apply
  ```

- Keep compatibility links (after renaming, create hardlink or symlink with the original name such as `audio.mp4`):
  ```
  python3 rename_allihoopa.py --root /path/to/archive --apply --keep-compat
  ```

- Force a dry-run even when `--apply` was intended (debug/testing):
  ```
  python3 rename_allihoopa.py --root /path/to/archive --apply --dry-run
  ```

- Undo based on the log file (default will actually move/delete unless `--dry-run` is provided):
  ```
  python3 rename_allihoopa.py --root /path/to/archive --undo
  ```
  Or dry-run the undo:
  ```
  python3 rename_allihoopa.py --root /path/to/archive --undo --dry-run
  ```

- Override the metadata path, pieces directory or log file:
  ```
  python3 rename_allihoopa.py \
    --meta /path/to/alltihop.json \
    --pieces-dir /path/to/assets/pieces \
    --log /path/to/rename_log.jsonl \
    --apply
  ```

- Override the username used in generated filenames:
  ```
  python3 rename_allihoopa.py --root /path/to/archive --username "SomeUser" --apply
  ```

- Preserve spaces instead of converting whitespace to underscores:
  ```
  python3 rename_allihoopa.py --root /path/to/archive --preserve-blanks --apply
  ```

---

## Command line options

- `--root` — Archive root (default `.`). Script expects `dump/` subdirectory under root by default.
- `--meta` — Path to `alltihop.json` (default: `<root>/dump/alltihop.json`).
- `--pieces-dir` — Pieces directory (default: `<root>/dump/assets/pieces`).
- `--log` — Log file (default: `<root>/rename_log.jsonl`).
- `--apply` — Actually perform renames; if omitted, script runs in dry-run mode.
- `--keep-compat` — After renaming, create hardlink or symlink at the original filename pointing to the new filename.
- `--undo` — Undo renames based on the log file.
- `--dry-run` — Force dry-run mode (also affects `--undo`).
- `--username` — Override username used in filenames (by default the script uses `user.username` or `user.display_name` from metadata).
- `--preserve-blanks` — Preserve spaces in output filenames (default is to convert whitespace to underscores).

---

## What the script logs

When renames are performed (not in dry-run), each operation is appended to the JSONL log file. Each line is a JSON object with fields similar to:

- `kind` — `"audio"`, `"cover"`, or `"attachment"`
- `short_id` — piece short id (directory name)
- `title` — piece title used
- `src` — original path (string)
- `dst` — new path (string)
- `keep_compat` — whether compatibility link was requested

The `--undo` command reads this log (from top to bottom, but it processes entries in reverse order) and moves files back or deletes new names depending on the `keep_compat` flag.

---

## Notes and caveats

- Dry-run is the safe default; always inspect planned operations before using `--apply`.
- The script attempts to be conservative:
  - It skips renaming if the destination already exists.
  - It uses a `unique_path` strategy to avoid overwriting: if the computed new filename exists, the script will append `__N` to the base to find a free name.
- Compatibility links:
  - Hardlinks are preferred; if hardlink creation fails (e.g., cross-device), the script attempts a relative symlink.
  - When `keep_compat` is used, undo will generally delete the new name but leave the original name (the compat link or original file) in place.
- Filename sanitization is designed to be Windows-safe as well as portable across POSIX systems.
- The script expects the metadata JSON to be in the Alltihop dump format. If the JSON file starts with `alltihop=` prefix or ends with a trailing semicolon, the loader will strip that before parsing.

---

If you want improvements or a packaged command (entrypoint), consider:
- Adding a setup script / setuptools entry point.
- Adding unit tests for filename sanitization and link creation.
- Adding an option to compute/display a summary (X renames, Y missing audio, Z missing cover) without listing each file.

If you need an example run for your archive, tell me the archive layout (root path) and whether you want a dry-run or actual apply, and I can provide the exact command to run.
