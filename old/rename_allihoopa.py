#!/usr/bin/env python3
# rename_allihoopa.py
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# INVALID_WIN_CHARS = r'<>:"/\\|?*\x00-\x1F'
# INVALID_WIN_RE = re.compile(f"[{re.escape(INVALID_WIN_CHARS)}]")

INVALID_WIN_RE = re.compile(r'[<>:"/\\|?*\u0000-\u001F]')

WS_RE = re.compile(r"\s+")

WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def load_alltihop_json(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if raw.startswith("alltihop="):
        raw = raw[len("alltihop="):]
    raw = raw.strip().rstrip(";")
    return json.loads(raw)


def safe_filename_base(s: str, max_len: int = 180) -> str:
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

    # replace illegal chars (Windows + path separators)
    s = INVALID_WIN_RE.sub("_", s)

    # remove trailing dots/spaces (Windows)
    s = s.rstrip(" .")

    # avoid reserved device names (also with extensions on Windows)
    # so protect the whole base, not just stem.
    if s.upper() in WIN_RESERVED:
        s = f"_{s}_"

    # cap length (leave room for extension)
    if len(s) > max_len:
        s = s[:max_len].rstrip(" .")

    return s or "untitled"


def unique_path(p: Path) -> Path:
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


@dataclass
class RenameOp:
    kind: str  # audio / cover / attachment
    short_id: str
    title: str
    src: Path
    dst: Path
    keep_compat: bool

    def to_json(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "short_id": self.short_id,
            "title": self.title,
            "src": str(self.src),
            "dst": str(self.dst),
            "keep_compat": self.keep_compat,
        }


def build_ops(
    pieces: List[Dict[str, Any]],
    pieces_dir: Path,
    username: str,
    preserve_blanks: bool,
    keep_compat: bool) -> Tuple[List[RenameOp], List[str]]:

    ops: List[RenameOp] = []
    warnings: List[str] = []

    username = safe_filename_base(username, preserve_blanks=preserve_blanks)

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

        base = safe_filename_base(f"{username} - {title}", preserve_blanks=preserve_blanks)

        # AUDIO: prefer common names / stem "audio"
        audio_src = None
        for cand in [folder / "audio.mp4", folder / "audio.m4a", folder / "audio.wav", folder / "audio.aac"]:
            if cand.exists():
                audio_src = cand
                break
        if audio_src is None:
            audio_src = find_by_stem(folder, "audio")

        if audio_src:
            dst = folder / f"{base}{audio_src.suffix}"
            if audio_src.name != dst.name and not dst.exists():
                ops.append(RenameOp("audio", sid, title, audio_src, unique_path(dst), keep_compat))
        else:
            warnings.append(f"[{sid}] audio file not found (expected stem 'audio')")

        # COVER: prefer cover.{jpg,jpeg,png} / stem "cover"
        cover_src = None
        for cand in [folder / "cover.jpg", folder / "cover.jpeg", folder / "cover.png"]:
            if cand.exists():
                cover_src = cand
                break
        if cover_src is None:
            cover_src = find_by_stem(folder, "cover")

        if cover_src:
            dst = folder / f"{base}{cover_src.suffix}"
            if cover_src.name != dst.name and not dst.exists():
                ops.append(RenameOp("cover", sid, title, cover_src, unique_path(dst), keep_compat))
        else:
            warnings.append(f"[{sid}] cover file not found (expected stem 'cover')")

        # ATTACHMENT (optional)
        if isinstance(attach, str) and attach.strip():
            attach_src = folder / attach
            if attach_src.exists():
                # keep attachment extension (e.g. ".figure")
                dst = folder / f"{base}{attach_src.suffix}"
                if attach_src.name != dst.name and not dst.exists():
                    ops.append(RenameOp("attachment", sid, title, attach_src, unique_path(dst), keep_compat))
            else:
                warnings.append(f"[{sid}] attachment listed in metadata but missing: {attach}")

    return ops, warnings


def apply_ops(ops: List[RenameOp], log_path: Path, dry_run: bool) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "DRY-RUN" if dry_run else "APPLY"

    print(f"{mode}: {len(ops)} rename operation(s)")
    if not ops:
        return

    if dry_run:
        for op in ops:
            print(f"  [{op.short_id}] {op.kind}: {op.src.name} -> {op.dst.name}")
            if op.keep_compat:
                print(f"             keep-compat: would keep '{op.src.name}' as a link to '{op.dst.name}'")
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

            if op.keep_compat:
                try:
                    ensure_compat_link(old_path, new_path)
                except Exception as e:
                    print(f"  WARN compat link failed for {old_path.name}: {e}")

            f.write(json.dumps(op.to_json(), ensure_ascii=False) + "\n")
            f.flush()


def undo_from_log(log_path: Path, dry_run: bool) -> None:
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

    entries.reverse()
    mode = "UNDO-DRY-RUN" if dry_run else "UNDO"
    print(f"{mode}: {len(entries)} operation(s) from log")

    for e in entries:
        src = Path(e["src"])
        dst = Path(e["dst"])
        keep_compat = bool(e.get("keep_compat", False))

        if keep_compat:
            # compat mode: we only delete the new name (old name stays as link/file)
            if dst.exists():
                print(f"  DEL  {dst}")
                if not dry_run:
                    dst.unlink()
            continue

        if dst.exists():
            print(f"  MOVE {dst} -> {src}")
            if not dry_run:
                if src.exists():
                    print(f"  WARN target exists, skipping: {src}")
                else:
                    dst.rename(src)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rename Allihoopa/Allihopopa piece assets based on dump/alltihop.json metadata."
    )
    ap.add_argument("--root", type=Path, default=Path("."), help="Archive root (contains dump/)")
    ap.add_argument("--meta", type=Path, default=None, help="Path to alltihop.json (default: <root>/dump/alltihop.json)")
    ap.add_argument("--pieces-dir", type=Path, default=None, help="Pieces dir (default: <root>/dump/assets/pieces)")
    ap.add_argument("--log", type=Path, default=None, help="Log file (default: <root>/rename_log.jsonl)")
    ap.add_argument("--apply", action="store_true", help="Actually rename files (otherwise dry-run)")
    ap.add_argument("--keep-compat", action="store_true",
                    help="After renaming, create hardlink/symlink with original names (audio.mp4/cover.jpg/...)")
    ap.add_argument("--undo", action="store_true", help="Undo based on log (see --log)")
    ap.add_argument("--dry-run", action="store_true", help="Force dry-run (also for --undo)")
    ap.add_argument("--username", type=str, default=None,
                    help="Override username used in filenames (default: metadata user.username)")
    ap.add_argument("--preserve-blanks", action="store_true",
                    help="Preserve spaces in output filenames (default: whitespace is replaced with underscores)."
)


    args = ap.parse_args()

    root = args.root.resolve()
    meta_path = (args.meta or (root / "dump" / "alltihop.json")).resolve()
    pieces_dir = (args.pieces_dir or (root / "dump" / "assets" / "pieces")).resolve()
    log_path = (args.log or (root / "rename_log.jsonl")).resolve()

    dry_run = args.dry_run or (not args.apply and not args.undo)

    if args.undo:
        undo_from_log(log_path, dry_run=dry_run)
        return 0

    if not meta_path.exists():
        print(f"Metadata file not found: {meta_path}")
        return 2
    if not pieces_dir.exists():
        print(f"Pieces dir not found: {pieces_dir}")
        return 2

    data = load_alltihop_json(meta_path)
    pieces = data.get("pieces")
    if not isinstance(pieces, list):
        print("Invalid metadata: expected key 'pieces' to be a list.")
        return 2

    meta_user = (data.get("user") or {})
    default_username = meta_user.get("username") or meta_user.get("display_name") or "unknown"
    username = args.username or default_username

    ops, warnings = build_ops(
        pieces,
        pieces_dir,
        username=username,
        keep_compat=args.keep_compat,
        preserve_blanks=args.preserve_blanks,
    )


    for w in warnings:
        print(f"WARN: {w}")

    apply_ops(ops, log_path=log_path, dry_run=dry_run)

    if not dry_run:
        print(f"Log written to: {log_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
