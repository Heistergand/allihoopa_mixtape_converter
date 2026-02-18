#!/usr/bin/env python3
# allihoopa_tool.py
from __future__ import annotations

import argparse
import json
import os, sys
import re
import shutil 
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

INVALID_WIN_RE = re.compile(r'[<>:"/\\|?*\u0000-\u001F]')
WS_RE = re.compile(r"\s+")

WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# -------------------------
# JSON loading
# -------------------------
def load_alltihop_json(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if raw.startswith("alltihop="):
        raw = raw[len("alltihop="):]
    raw = raw.strip().rstrip(";")
    return json.loads(raw)


# -------------------------
# Filename helpers
# -------------------------
def safe_filename_base(s: str, *, max_len: int = 180, preserve_blanks: bool = False) -> str:
    """
    Portable, human-readable filename base (keeps Unicode).
    Default: replace whitespace with underscores.
    With preserve_blanks=True: keep spaces (normalized).
    """
    s = (s or "").strip()

    if preserve_blanks:
        s = WS_RE.sub(" ", s)   # normalize to single space
    else:
        s = WS_RE.sub("_", s)   # linux-style: whitespace -> underscore
        s = re.sub(r"_+", "_", s)  # collapse runs of underscores

    if not s:
        s = "untitled"

    # replace illegal chars (Windows + path separators + control chars)
    s = INVALID_WIN_RE.sub("_", s)

    # remove trailing dots/spaces (Windows)
    s = s.rstrip(" .")

    # avoid reserved device names
    if s.upper() in WIN_RESERVED:
        s = f"_{s}_"

    # cap length (leave room for extension)
    if len(s) > max_len:
        s = s[:max_len].rstrip(" .")

    return s or "untitled"


def unique_path(p: Path) -> Path:
    """If p exists, append __2, __3, ... before suffix."""
    if not p.exists():
        return p
    stem, suf = p.stem, p.suffix
    i = 2
    while True:
        cand = p.with_name(f"{stem}__{i}{suf}")
        if not cand.exists():
            return cand
        i += 1


def find_by_stem(folder: Path, stem: str) -> Optional[Path]:
    for p in folder.iterdir():
        if p.is_file() and p.stem.lower() == stem.lower():
            return p
    return None


def ensure_compat_link(old_path: Path, new_path: Path) -> None:
    """
    Create a hardlink (preferred) or symlink at old_path pointing to new_path.
    """
    if old_path.exists():
        return

    try:
        os.link(new_path, old_path)
        return
    except Exception:
        pass

    try:
        rel = os.path.relpath(new_path, start=old_path.parent)
        os.symlink(rel, old_path)
        return
    except Exception as e:
        raise RuntimeError(f"Could not create compat link {old_path.name} -> {new_path.name}: {e}") from e


# -------------------------
# Comment/tag helpers
# -------------------------
def build_comment(description: Optional[str], collaborators: Optional[List[str]], username: Optional[str]) -> str:
    desc = (description or "").rstrip()

    user_norm = (username or "").strip().casefold()
    cols_raw = collaborators or []

    cols: List[str] = []
    seen: set[str] = set()
    for c in cols_raw:
        if not c:
            continue
        c_clean = str(c).strip()
        if not c_clean:
            continue
        c_norm = c_clean.casefold()

        # drop own username
        if user_norm and c_norm == user_norm:
            continue

        # de-duplicate (preserve order)
        if c_norm in seen:
            continue
        seen.add(c_norm)
        cols.append(c_clean)

    if not cols:
        return desc

    tail = "Collaborators:\n" + "\n".join(cols)
    return (desc + "\n\n" + tail) if desc else tail


def parse_tempo_to_tmpo(tempo_value: Any) -> Optional[int]:
    """
    MP4 'tmpo' expects an integer BPM.
    Input may be like "105.000".
    """
    if tempo_value is None:
        return None
    try:
        f = float(str(tempo_value).strip())
        if f <= 0:
            return None
        return int(round(f))
    except Exception:
        return None


# -------------------------
# Rename command
# -------------------------
@dataclass
class RenameOp:
    kind: str  # audio / cover / attachment
    short_id: str
    title: str
    src: Path
    dst: Path
    keep_mode: str  # "none" | "link" | "copy"

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "short_id": self.short_id,
            "title": self.title,
            "src": str(self.src),
            "dst": str(self.dst),
            "keep_link": self.keep_mode,
        }


def build_rename_ops(
    pieces: List[Dict[str, Any]],
    pieces_dir: Path,
    username: str,
    preserve_blanks: bool,
    keep_mode: str) -> Tuple[List[RenameOp], List[str]]:
    ops: List[RenameOp] = []
    warnings: List[str] = []

    username_clean = safe_filename_base(username, preserve_blanks=preserve_blanks)

    for piece in pieces:
        sid = piece.get("short_id")
        title = piece.get("title") or "untitled"
        attach = piece.get("attachment")  # e.g. "piece.figure" or null

        if not sid:
            warnings.append("Piece without short_id in metadata; skipping.")
            continue

        folder = pieces_dir / sid
        if not folder.exists():
            warnings.append(f"[{sid}] Folder missing: {folder}")
            continue

        base = safe_filename_base(f"{username_clean} - {title}", preserve_blanks=preserve_blanks)

        # AUDIO: prefer common names / stem "audio"
        audio_src: Optional[Path] = None
        for cand in [folder / "audio.mp4", folder / "audio.m4a", folder / "audio.wav", folder / "audio.aac"]:
            if cand.exists():
                audio_src = cand
                break
        if audio_src is None:
            audio_src = find_by_stem(folder, "audio")

        if audio_src:
            dst = folder / f"{base}{audio_src.suffix}"
            if audio_src.name != dst.name and not dst.exists():
                ops.append(RenameOp("audio", sid, title, audio_src, unique_path(dst), keep_mode))
        else:
            warnings.append(f"[{sid}] audio file not found (expected stem 'audio')")

        # COVER: prefer cover.{jpg,jpeg,png} / stem "cover"
        cover_src: Optional[Path] = None
        for cand in [folder / "cover.jpg", folder / "cover.jpeg", folder / "cover.png"]:
            if cand.exists():
                cover_src = cand
                break
        if cover_src is None:
            cover_src = find_by_stem(folder, "cover")

        if cover_src:
            # New naming: "<username> - <title>.cover.<ext>"
            dst = folder / f"{base}.cover{cover_src.suffix}"
            if cover_src.name != dst.name and not dst.exists():
                ops.append(RenameOp("cover", sid, title, cover_src, unique_path(dst), keep_mode))
        else:
            warnings.append(f"[{sid}] cover file not found (expected stem 'cover')")

        # ATTACHMENT (optional)
        if isinstance(attach, str) and attach.strip():
            attach_src = folder / attach
            if attach_src.exists():
                dst = folder / f"{base}{attach_src.suffix}"
                if attach_src.name != dst.name and not dst.exists():
                    ops.append(RenameOp("attachment", sid, title, attach_src, unique_path(dst), keep_mode))
            else:
                warnings.append(f"[{sid}] attachment listed in metadata but missing: {attach}")

    return ops, warnings


def apply_rename_ops(ops: List[RenameOp], log_path: Path, dry_run: bool) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "DRY-RUN" if dry_run else "APPLY"

    print(f"{mode}: {len(ops)} rename operation(s)")
    if not ops:
        return

    if dry_run:
        for op in ops:
            print(f"  [{op.short_id}] {op.kind}: {op.src.name} -> {op.dst.name}")
            if op.keep_mode == "link":  
                print(f"             keep: would keep '{op.src.name}' as a link to '{op.dst.name}'")
            elif op.keep_mode == "copy":
                print(f"             keep: would keep '{op.src.name}' as a copy of '{op.dst.name}'")
                
            print("(No changes made. Use --apply to execute.)")                
        return

    with log_path.open("a", encoding="utf-8") as f:
        for op in ops:
            if op.dst.exists():
                print(f"  SKIP exists: {op.dst}")
                continue

            old_path = op.src
            new_path = op.dst

            print(f"  DO   [{op.short_id}] {op.kind}: {old_path.name} -> {new_path.name}")
            old_path.rename(new_path)

            if op.keep_mode == "link":
                try:
                    ensure_compat_link(old_path, new_path)
                except Exception as e:
                    print(f"  WARN keep link failed for {old_path.name}: {e}")
            elif op.keep_mode == "copy":
                try:
                    ensure_compat_copy(old_path, new_path)
                except Exception as e:
                    print(f"  WARN keep copy failed for {old_path.name}: {e}")

            if op.keep_link:
                try:
                    ensure_compat_link(old_path, new_path)
                except Exception as e:
                    print(f"  WARN compat link failed for {old_path.name}: {e}")

            f.write(json.dumps(op.to_json(), ensure_ascii=False) + "\n")
            f.flush()


def undo_rename_from_log(log_path: Path, dry_run: bool) -> None:
    if not log_path.exists():
        print(f"Log not found: {log_path}")
        return

    entries: List[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                pass

    if not entries:
        print("No log entries found.")
        return

    # reverse order (important)
    entries.reverse()
    mode = "UNDO-DRY-RUN" if dry_run else "UNDO"
    print(f"{mode}: {len(entries)} operation(s) from log")

    for e in entries:
        src = Path(e["src"])  # old name (compat name)
        dst = Path(e["dst"])  # renamed file (authoritative)

        if not dst.exists():
            # We assume dst is the authoritative "good" file; if it's missing we can't restore safely.
            print(f"  WARN dst missing, skipping: {dst}")
            continue

        # Delete compat file (if present), regardless of symlink/hardlink/copy.
        if src.exists() or src.is_symlink():
            # src.is_symlink() covers broken symlinks on some platforms.
            if src.is_dir():
                print(f"  WARN src is a directory, not deleting: {src}")
            else:
                print(f"  DEL  {src}")
                if not dry_run:
                    try:
                        src.unlink()
                    except PermissionError:
                        # best-effort: clear readonly bit and retry (Windows)
                        try:
                            os.chmod(src, 0o666)
                            src.unlink()
                        except Exception as ex:
                            print(f"  WARN could not delete {src}: {ex}")
                    except Exception as ex:
                        print(f"  WARN could not delete {src}: {ex}")

        # Now rename the authoritative file back to the original name.
        # After deleting src, the path should be free.
        print(f"  MOVE {dst} -> {src}")
        if not dry_run:
            try:
                dst.rename(src)
            except Exception as ex:
                print(f"  WARN could not rename {dst} -> {src}: {ex}")
    

# -------------------------
# Tag command
# -------------------------
AUDIO_EXTS = (".mp4", ".m4a", ".aac")
COVER_EXTS = (".jpg", ".jpeg", ".png")


def find_audio_file(folder: Path, base: str) -> Optional[Path]:
    # 1) Prefer renamed form "<base>.<ext>"
    for ext in AUDIO_EXTS:
        p = folder / f"{base}{ext}"
        if p.exists():
            return p

    # 2) Fallback: legacy "audio.*" / stem audio
    for ext in (".mp4", ".m4a", ".aac", ".wav", ".flac", ".mp3", ".ogg"):
        p = folder / f"audio{ext}"
        if p.exists():
            return p
    p = find_by_stem(folder, "audio")
    if p:
        return p

    # 3) If exactly one MP4/M4A in folder, use it
    candidates = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    if len(candidates) == 1:
        return candidates[0]

    return None


def find_cover_file(folder: Path, base: str) -> Optional[Path]:
    # 1) Prefer renamed form "<base>.cover.<ext>"
    for ext in COVER_EXTS:
        p = folder / f"{base}.cover{ext}"
        if p.exists():
            return p

    # 2) Fallback: legacy "cover.*" / stem cover
    for ext in COVER_EXTS:
        p = folder / f"cover{ext}"
        if p.exists():
            return p
    p = find_by_stem(folder, "cover")
    if p:
        return p

    # 3) If exactly one image in folder, use it
    candidates = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in COVER_EXTS]
    if len(candidates) == 1:
        return candidates[0]

    return None


def write_meta_file(meta_path: Path, piece: Dict[str, Any], *, overwrite: bool) -> None:
    if meta_path.exists() and not overwrite:
        return
    meta_path.write_text(json.dumps(piece, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tag_mp4_file(
    audio_path: Path,
    *,
    title: str,
    artist: str,
    comment: str,
    created_at: Optional[str],
    tempo_value: Any,
    cover_path: Optional[Path],
    alltihop_piece_json: Dict[str, Any],
) -> None:
    """
    Writes MP4/M4A tags and embeds cover art (if provided) using mutagen.
    Also writes a freeform custom tag:
      ----:com.apple.iTunes:alltihop_json = <full piece JSON as UTF-8>
    """
    try:
        from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Tagging requires the 'mutagen' package. Install it via:\n"
            "  python -m pip install mutagen\n"
            f"Import error: {e}"
        ) from e

    mp4 = MP4(str(audio_path))
    if mp4.tags is None:
        mp4.add_tags()

    tags = mp4.tags

    # Standard-ish fields
    tags["\xa9nam"] = [title]               # Title
    tags["\xa9ART"] = [artist]              # Artist
    if comment:
        tags["\xa9cmt"] = [comment]         # Comment

    if created_at:
        # Many tools accept ISO date here; keeping full timestamp is usually fine.
        tags["\xa9day"] = [created_at]

    tmpo = parse_tempo_to_tmpo(tempo_value)
    if tmpo is not None:
        tags["tmpo"] = [tmpo]

    # Cover art
    if cover_path and cover_path.exists():
        b = cover_path.read_bytes()
        suf = cover_path.suffix.lower()
        if suf in (".jpg", ".jpeg"):
            tags["covr"] = [MP4Cover(b, imageformat=MP4Cover.FORMAT_JPEG)]
        elif suf == ".png":
            tags["covr"] = [MP4Cover(b, imageformat=MP4Cover.FORMAT_PNG)]
        # else: ignore (shouldn't happen due to finder)

    # Custom freeform JSON
    piece_json_bytes = (json.dumps(alltihop_piece_json, ensure_ascii=False) + "\n").encode("utf-8")
    tags["----:com.apple.iTunes:alltihop_json"] = [MP4FreeForm(piece_json_bytes)]

    mp4.save()


def ensure_compat_copy(old_path: Path, new_path: Path) -> None:
    """
    Create a copy at old_path from new_path.
    """
    if old_path.exists():
        return
    shutil.copy2(new_path, old_path)


def cmd_tag(
    *,
    data: Dict[str, Any],
    pieces_dir: Path,
    username: str,
    preserve_blanks: bool,
    dry_run: bool,
    overwrite_meta: bool,
) -> int:
    pieces = data.get("pieces")
    if not isinstance(pieces, list):
        print("Invalid metadata: expected key 'pieces' to be a list.")
        return 2
        
        
    mutagen_missing = False
    if dry_run:
        try:
            from mutagen.mp4 import MP4  # noqa: F401
        except Exception:
            mutagen_missing = True


    username_clean = safe_filename_base(username, preserve_blanks=preserve_blanks)

    planned = 0
    warnings: List[str] = []

    for piece in pieces:
        sid = piece.get("short_id")
        title = piece.get("title") or "untitled"
        description = piece.get("description")
        collaborators = piece.get("collaborators")
        created_at = piece.get("created_at")
        tempo_value = piece.get("tempo")

        if not sid:
            warnings.append("Piece without short_id in metadata; skipping.")
            continue

        folder = pieces_dir / sid
        if not folder.exists():
            warnings.append(f"[{sid}] Folder missing: {folder}")
            continue

        base = safe_filename_base(f"{username_clean} - {title}", preserve_blanks=preserve_blanks)

        audio_path = find_audio_file(folder, base)
        if not audio_path:
            warnings.append(f"[{sid}] audio not found; skipping tagging.")
            continue

        if audio_path.suffix.lower() not in AUDIO_EXTS:
            warnings.append(f"[{sid}] audio '{audio_path.name}' is not MP4/M4A/AAC; skipping MP4 tagging.")
            continue

        cover_path = find_cover_file(folder, base)
        if not cover_path:
            warnings.append(f"[{sid}] cover not found; tagging will proceed without embedded artwork.")

        meta_path = folder / f"{base}.meta.json"

        comment = build_comment(
            description=description if isinstance(description, str) else None,
            collaborators=collaborators if isinstance(collaborators, list) else None,
            username=username,
        )

        planned += 1

        if dry_run:
            print(f"[{sid}] TAG: {audio_path.name}")
            if cover_path:
                print(f"       cover: {cover_path.name} (will embed)")
            else:
                print(f"       cover: (none)")
            print(f"       meta : {meta_path.name} (will write{' (overwrite)' if overwrite_meta else ''})")
            # show a short preview of comment
            if comment:
                preview = comment.replace("\n", "\\n")
                if len(preview) > 120:
                    preview = preview[:120] + "..."
                print(f"       comment: {preview}")
            print(f"       custom : ----:com.apple.iTunes:alltihop_json (full piece JSON)")
            continue

        # APPLY
        try:
            write_meta_file(meta_path, piece, overwrite=overwrite_meta)
            tag_mp4_file(
                audio_path,
                title=title,
                artist=username,
                comment=comment,
                created_at=created_at if isinstance(created_at, str) else None,
                tempo_value=tempo_value,
                cover_path=cover_path,
                alltihop_piece_json=piece,
            )
            print(f"[{sid}] OK: tagged {audio_path.name} + wrote {meta_path.name}")
        except Exception as e:
            warnings.append(f"[{sid}] ERROR: {e}")

    for w in warnings:
        print(f"WARN: {w}")

    if dry_run:
        print(f"DRY-RUN: planned tagging for {planned} piece(s). Use --apply to execute.")

        if mutagen_missing:
            msg = (
                "WARNING: Python module 'mutagen' is not installed. "
                "Tagging in --apply mode will fail.\n"
                "Install it with:\n"
                "  python -m pip install mutagen"
            )
            # red output (best-effort)
            RED = "\033[31m"
            RESET = "\033[0m"
            if sys.stderr.isatty() and os.getenv("NO_COLOR") is None:
                print(f"{RED}{msg}{RESET}", file=sys.stderr)
            else:
                print(msg, file=sys.stderr)

    else:
        print(f"APPLY: processed {planned} piece(s).")

    return 0


# -------------------------
# CLI
# -------------------------
def build_cli() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Allihoopa export archive tool: rename assets and embed tags into MP4/M4A.",
        epilog=(
            "Subcommands:\n"
            "  rename   Rename files inside piece folders\n"
            "  tag      Write sidecar *.meta.json and embed tags/cover into MP4/M4A\n\n"
            "More help:\n"
            "  allihoopa_tool.py rename -h\n"
            "  allihoopa_tool.py tag -h\n\n"
            "Note: global options must be placed BEFORE the subcommand, e.g.:\n"
            "  allihoopa_tool.py --root <path> rename --apply"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    ap.add_argument("--root", type=Path, default=Path("."), help="Archive root (contains dump/)")
    ap.add_argument("--meta", type=Path, default=None, help="Path to export metadata (default: <root>/dump/alltihop.json)")
    ap.add_argument("--pieces-dir", type=Path, default=None, help="Pieces directory (default: <root>/dump/assets/pieces)")
    ap.add_argument("--username", type=str, default=None, help="Override username (default: from export metadata)")
    ap.add_argument("--preserve-blanks", action="store_true",
                    help="Preserve spaces in output filenames (default: whitespace is replaced with underscores).")

    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_r = sub.add_parser("rename", help="Rename audio/cover/attachments in each piece folder.")
    ap_r.add_argument("--log", type=Path, default=None, help="Log file (default: <root>/rename_log.jsonl)")
    ap_r.add_argument("--apply", action="store_true", help="Actually rename files (otherwise dry-run)")
    ap_r.add_argument("--dry-run", action="store_true", help="Force dry-run")
                      
    mx = ap_r.add_mutually_exclusive_group()
    mx.add_argument(
        "--keep-link",
        action="store_true",
        help="After renaming, keep the original names as hardlink/symlink (audio.mp4/cover.jpg/...).",
    )
    mx.add_argument(
        "--keep-copy",
        action="store_true",
        help="After renaming, keep the original names as copies (audio.mp4/cover.jpg/...).",
    )

    ap_r.add_argument("--undo", action="store_true", help="Undo renames based on log (see --log)")

    ap_t = sub.add_parser("tag", help="Embed tags+cover into MP4/M4A and write *.meta.json sidecar files.")
    ap_t.add_argument("--apply", action="store_true", help="Actually write tags/files (otherwise dry-run)")
    ap_t.add_argument("--dry-run", action="store_true", help="Force dry-run")
    ap_t.add_argument("--no-overwrite-meta", action="store_true",
                      help="Do not overwrite existing *.meta.json files.")

    return ap



def main() -> int:
    ap = build_cli()
    args = ap.parse_args()

    root = args.root.resolve()
    meta_path = (args.meta or (root / "dump" / "alltihop.json")).resolve()
    pieces_dir = (args.pieces_dir or (root / "dump" / "assets" / "pieces")).resolve()

    if not meta_path.exists():
        print(f"Metadata file not found: {meta_path}")
        return 2
    if not pieces_dir.exists():
        print(f"Pieces dir not found: {pieces_dir}")
        return 2

    data = load_alltihop_json(meta_path)

    meta_user = (data.get("user") or {})
    default_username = meta_user.get("username") or meta_user.get("display_name") or "unknown"
    username = args.username or default_username

    if args.cmd == "rename":
        log_path = (args.log or (root / "rename_log.jsonl")).resolve()
        dry_run = args.dry_run or (not args.apply and not args.undo)

        if args.undo:
            undo_rename_from_log(log_path, dry_run=dry_run)
            return 0

        pieces = data.get("pieces")
        if not isinstance(pieces, list):
            print("Invalid metadata: expected key 'pieces' to be a list.")
            return 2

        keep_mode = "none"
        if args.keep_link:
            keep_mode = "link"
        elif args.keep_copy:
            keep_mode = "copy"

        ops, warnings = build_rename_ops(
            pieces,
            pieces_dir,
            username=username,
            preserve_blanks=args.preserve_blanks,
            keep_mode=args.keep_mode,
        )

        for w in warnings:
            print(f"WARN: {w}")

        apply_rename_ops(ops, log_path=log_path, dry_run=dry_run)

        if not dry_run:
            print(f"Log written to: {log_path}")
        return 0

    if args.cmd == "tag":
        dry_run = args.dry_run or (not args.apply)
        overwrite_meta = not args.no_overwrite_meta

        return cmd_tag(
            data=data,
            pieces_dir=pieces_dir,
            username=username,
            preserve_blanks=args.preserve_blanks,
            dry_run=dry_run,
            overwrite_meta=overwrite_meta,
        )

    print("Unknown command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
