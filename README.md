# Allihoopa Mixtape Archive Tool

A small CLI tool to clean up and preserve an exported *Allihoopa “Mixtape”* archive.

It can:

- **Rename** audio/cover/attachment files based on export metadata.
- **Generate sidecar metadata files** per piece folder: `Username - Title.meta.json`
- **Embed tags + cover art into MP4/M4A** where possible, and store the full per-piece JSON inside the file as a custom MP4 tag field (`alltihop_json`).

## What was Allihoopa?

Allihoopa was an online music collaboration platform. In December 2018, users could download their complete data as an archive (“Mixtape”). The service was shut down in 2019.  
This tool is meant to make those Mixtape exports easier to manage long-term.

---

# File Naming

After renaming, each piece folder will look like:

- `Username - Title.mp4` (or `.m4a/.aac` if the export contains those)
- `Username - Title.cover.jpg` (or `.png/.jpeg`)
- `Username - Title.meta.json`

Whitespace handling:

- **Default:** spaces are replaced with underscores (shell-friendly)
- With `--preserve-blanks`: keep spaces (normalized to single spaces)

Examples:

- Default: `Username_-_Title.mp4`
- With `--preserve-blanks`: `Username - Title.mp4`

---

# Usage

## Folder structure assumptions

Default expected structure (typical Mixtape export):

- `dump/alltihop.json` (export metadata)
- `dump/assets/pieces/<short_id>/...` (one folder per piece)

You can override paths via CLI options.

## Help

Top-level help:

```bash
python3 allihoopa_tool.py -h
```

Per-command help:

```bash
python3 allihoopa_tool.py rename -h
python3 allihoopa_tool.py tag -h
```

## Important CLI rule (argparse subcommands)

Global options must be placed **before** the subcommand:

✅ Works:

```bash
python3 allihoopa_tool.py --root ./my_export rename --apply
python3 allihoopa_tool.py --root ./my_export tag --apply
```

❌ Does not work:

```bash
python3 allihoopa_tool.py rename --root ./my_export --apply
```

## Rename (dry-run first)

Preview what would change:

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> rename
```

Apply renames:

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> rename --apply
```

### Keeping legacy names: link vs copy

Some older tooling expects legacy names like `audio.mp4` / `cover.jpg` in each piece folder.
You can keep those legacy names *in addition* to the renamed files in one of two ways:

**A) Keep legacy names as links**

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> rename --apply --keep-link
```

Notes:
- The tool tries to create **hardlinks** first, then **symlinks** as a fallback.
- Link behavior depends on filesystem and permissions.
- Some ZIP tools / cloud sync services may not preserve symlinks.

**B) Keep legacy names as copies**

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> rename --apply --keep-copy
```

Notes:
- Copies are larger (extra disk usage), but portable and ZIP-friendly.

`--keep-link` and `--keep-copy` are mutually exclusive.

### Undo renames

Renames are logged to `rename_log.jsonl`. Undo using:

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> rename --undo
```

Preview undo:

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> rename --undo --dry-run
```

Undo behavior:
- The renamed file (`dst` in the log) is treated as authoritative.
- Any legacy/compat file at the original path is deleted (if present), then `dst` is renamed back.

## Tagging + sidecar metadata

Tagging does:

- Writes `Username - Title.meta.json` in each piece folder (full piece JSON).
- Embeds common tags into MP4/M4A (Title, Artist, Comment/Description, Date, BPM if possible).
- Embeds cover art into the MP4/M4A (if a cover image is found).
- Stores the full per-piece JSON as a custom MP4 freeform field: `----:com.apple.iTunes:alltihop_json`

Dry-run:

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> tag
```

Apply:

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> tag --apply
```

Do not overwrite existing `*.meta.json`:

```bash
python3 allihoopa_tool.py --root <EXPORT_ROOT> tag --apply --no-overwrite-meta
```

### Comment formatting (Collaborators)

The tool appends collaborators under the description with a blank line and a heading:

```
<description>

Collaborators:
Name1
Name2
...
```

If your own username appears in `collaborators`, it is removed from that list to avoid duplicates.

---

# Installation

> [!TIP]
> ### Consider working in a virtual environment
> ```bash
> python3 -m venv .venv
> source .venv/bin/activate
> ```
>
> Learn more: [Python Virtual Environment](https://www.w3schools.com/python/python_virtualenv.asp)

## Requirements

- Python 3.9+ recommended
- A shell/terminal environment

## Dependencies

- `rename` works without extra packages
- `tag` requires **mutagen** (MP4/M4A tagging + cover embedding)

Install mutagen:

```bash
python -m pip install mutagen
```

Tip: run `tag` once in dry-run mode. The tool can warn you if `mutagen` is missing.



---

# Notes & Limitations

## MP4/M4A tagging support varies by player

- Most players show Title/Artist/Comment/Artwork.
- The custom JSON field (`alltihop_json`) is primarily for archival/tooling. Many players will not display it, but it stays embedded in the file.

---
