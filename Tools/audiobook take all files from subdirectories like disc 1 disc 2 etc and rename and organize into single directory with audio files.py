#!/usr/bin/env python3
"""
Universal Audiobook File Renamer

Flow:
  1. Ask for directory, author, title
  2. Detect all subdirectories → show them as an ordered disc list
  3. Interactive disc-order review  (swap / move / remove / show tracks / rename disc)
  4. Full file preview with first+last files shown
  5. Confirm → rename + cleanup
"""

import os
import re
import shutil
from pathlib import Path
from typing import List, Tuple


# ═══════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════

def natural_sort_key(path: Path) -> list:
    return [
        int(c) if c.isdigit() else c.lower()
        for c in re.split(r'(\d+)', path.name)
    ]


def get_audio_files(folder: Path) -> List[Path]:
    exts = {'.mp3', '.m4a', '.m4b', '.flac', '.wav', '.aac', '.ogg', '.wma'}
    files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts]
    files.sort(key=natural_sort_key)
    return files


def sanitize(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def hr(char='─', width=60):
    print(char * width)


# ═══════════════════════════════════════════════════════════
#  Step 1 – Collect
# ═══════════════════════════════════════════════════════════

def collect_disc_folders(root: Path) -> List[Path]:
    """Return all subdirs that contain at least one audio file, naturally sorted."""
    subdirs = sorted([d for d in root.iterdir() if d.is_dir()], key=natural_sort_key)
    return [d for d in subdirs if get_audio_files(d)]


# ═══════════════════════════════════════════════════════════
#  Step 2 – Interactive disc-order review
# ═══════════════════════════════════════════════════════════

DISC_HELP = """
  Commands:
    Enter          Continue to file preview
    show <N>       Show all tracks in disc N
    swap <A> <B>   Swap two discs  (e.g.  swap 2 3)
    move <A> <B>   Move disc A to position B
    remove <N>     Drop disc N from the list entirely
    rename <N>     Change the display label for disc N
    q / cancel     Quit without making any changes
"""

def print_disc_table(discs: List[Path], labels: List[str], root: Path):
    hr()
    print(f"  {'#':>3}   {'Tracks':>6}   Folder")
    hr('─')
    for i, (d, lbl) in enumerate(zip(discs, labels), 1):
        files = get_audio_files(d)
        # Show custom label if it differs from actual name
        display = lbl if lbl != d.name else d.name
        # Truncate long names for readability
        if len(display) > 50:
            display = display[:47] + '...'
        print(f"  {i:>3}   {len(files):>6}   {display}")
    hr()


def disc_review(discs: List[Path], root: Path) -> Tuple[List[Path], List[str]]:
    """
    Interactive loop for reviewing / reordering disc folders.
    Returns (ordered_discs, labels) or raises SystemExit on cancel.
    """
    # Labels start as folder names; user can rename them for clarity
    labels = [d.name for d in discs]

    print(f"\n  Detected {len(discs)} disc folder(s) in natural sort order:")
    print_disc_table(discs, labels, root)
    print(DISC_HELP)

    while True:
        raw = input("  disc> ").strip()

        # ── blank → accept ──────────────────────────────────
        if raw == '':
            if not discs:
                print("  No discs left — nothing to rename. Cancelling.")
                raise SystemExit
            return discs, labels

        # ── quit ────────────────────────────────────────────
        if raw.lower() in ('q', 'quit', 'cancel'):
            print("  Cancelled.")
            raise SystemExit

        parts = raw.split()
        cmd = parts[0].lower()

        # ── show <N> ────────────────────────────────────────
        if cmd == 'show' and len(parts) == 2:
            n = _parse_index(parts[1], len(discs))
            if n is None:
                continue
            files = get_audio_files(discs[n])
            print(f"\n  Disc {n+1} — {labels[n]}  ({len(files)} tracks)")
            for j, f in enumerate(files, 1):
                print(f"    {j:>3}.  {f.name}")
            print()
            continue

        # ── swap <A> <B> ────────────────────────────────────
        if cmd == 'swap' and len(parts) == 3:
            a = _parse_index(parts[1], len(discs))
            b = _parse_index(parts[2], len(discs))
            if a is None or b is None:
                continue
            discs[a], discs[b] = discs[b], discs[a]
            labels[a], labels[b] = labels[b], labels[a]
            print(f"  Swapped disc {a+1} and disc {b+1}.")
            print_disc_table(discs, labels, root)
            continue

        # ── move <A> <B> ────────────────────────────────────
        if cmd == 'move' and len(parts) == 3:
            a = _parse_index(parts[1], len(discs))
            b = _parse_index(parts[2], len(discs))
            if a is None or b is None:
                continue
            disc = discs.pop(a)
            lbl  = labels.pop(a)
            discs.insert(b, disc)
            labels.insert(b, lbl)
            print(f"  Moved disc to position {b+1}.")
            print_disc_table(discs, labels, root)
            continue

        # ── remove <N> ──────────────────────────────────────
        if cmd == 'remove' and len(parts) == 2:
            n = _parse_index(parts[1], len(discs))
            if n is None:
                continue
            removed = labels[n]
            discs.pop(n)
            labels.pop(n)
            print(f"  Removed '{removed}' from the list.")
            print_disc_table(discs, labels, root)
            continue

        # ── rename <N> ──────────────────────────────────────
        if cmd == 'rename' and len(parts) == 2:
            n = _parse_index(parts[1], len(discs))
            if n is None:
                continue
            new_label = input(f"  New label for disc {n+1} (was '{labels[n]}'): ").strip()
            if new_label:
                labels[n] = new_label
                print(f"  Label updated.")
                print_disc_table(discs, labels, root)
            continue

        # ── unknown ─────────────────────────────────────────
        print(f"  Unknown command: '{raw}'.  Type Enter to continue or 'q' to cancel.")
        print(DISC_HELP)


def _parse_index(token: str, length: int):
    """Convert 1-based user input to 0-based index with bounds check."""
    try:
        n = int(token)
        if not (1 <= n <= length):
            raise ValueError
        return n - 1
    except ValueError:
        print(f"  '{token}' is not a valid disc number (1–{length}).")
        return None


# ═══════════════════════════════════════════════════════════
#  Step 3 – File preview
# ═══════════════════════════════════════════════════════════

def file_preview(all_files: List[Path], author: str, title: str, root: Path,
                 discs: List[Path], labels: List[str]) -> bool:
    """
    Show a disc-by-disc summary and sample filenames.
    Returns True to proceed, False to cancel.
    """
    total = len(all_files)

    print(f"\n  Output: {author} - {title} - ###<ext>")
    print(f"  Total files: {total}\n")

    # Per-disc breakdown with track range
    counter = 1
    for disc, lbl in zip(discs, labels):
        files = get_audio_files(disc)
        if not files:
            continue
        start, end = counter, counter + len(files) - 1
        display = lbl if len(lbl) <= 45 else lbl[:42] + '...'
        print(f"  Disc  {display}")
        print(f"        tracks {start:03d} – {end:03d}  ({len(files)} files)")
        counter += len(files)

    # Also account for any root-level files at the top
    root_files = [f for f in all_files if f.parent == root]
    if root_files:
        print(f"\n  Root-level files: {len(root_files)} (numbered first)")

    print()
    hr()

    # Sample: first 3 and last 3
    samples = []
    if total <= 6:
        samples = list(enumerate(all_files))
    else:
        samples = list(enumerate(all_files[:3])) + [None] + list(enumerate(all_files[-3:], total - 3))

    print(f"  {'#':>5}   {'Source file':<35}  {'New name'}")
    hr('─')
    for item in samples:
        if item is None:
            print(f"  {'...':>5}   {'...':35}  ...")
            continue
        i, f = item
        new = f"{author} - {title} - {i+1:03d}{f.suffix}"
        src = f.name if len(f.name) <= 35 else f.name[:32] + '...'
        print(f"  {i+1:>5}   {src:<35}  {new}")
    hr()

    print()
    print("  Options:")
    print("    Enter        Proceed with renaming")
    print("    b / back     Go back to disc order review")
    print("    q / cancel   Quit without making any changes")
    print()

    while True:
        choice = input("  file> ").strip().lower()
        if choice == '':
            return True
        if choice in ('b', 'back'):
            return None   # signal to go back
        if choice in ('q', 'quit', 'cancel'):
            print("  Cancelled.")
            raise SystemExit
        print("  Enter, 'b' to go back, or 'q' to cancel.")


# ═══════════════════════════════════════════════════════════
#  Step 4 – Rename
# ═══════════════════════════════════════════════════════════

def rename_files(all_files: List[Path], author: str, title: str,
                 root: Path, disc_folders: List[Path]) -> None:
    moved, errors = [], []

    print()
    for i, src in enumerate(all_files, 1):
        new_name = f"{author} - {title} - {i:03d}{src.suffix}"
        dest = root / new_name

        if src.parent == root and src.name == new_name:
            moved.append(dest)
            continue

        if dest.exists() and dest != src:
            msg = f"Skipped (target exists): {new_name}"
            print(f"  ! {msg}")
            errors.append(msg)
            continue

        try:
            shutil.move(str(src), str(dest))
            print(f"  {src.name}  →  {new_name}")
            moved.append(dest)
        except Exception as e:
            msg = f"{src.name}: {e}"
            print(f"  ERROR {msg}")
            errors.append(msg)

    # Remove empty disc folders
    removed = []
    for d in disc_folders:
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed.append(d.name)
                print(f"  Removed empty folder: {d.name}")
            else:
                leftover = [f.name for f in d.iterdir()]
                print(f"  Left non-empty folder: {d.name}  {leftover}")
        except Exception as e:
            errors.append(f"Could not remove {d.name}: {e}")

    print()
    hr('═')
    print(f"  Done!  {len(moved)} file(s) renamed,  {len(removed)} empty folder(s) removed.")
    if errors:
        print(f"  {len(errors)} warning(s):")
        for e in errors:
            print(f"    - {e}")
    hr('═')


# ═══════════════════════════════════════════════════════════
#  Step 0 – Input
# ═══════════════════════════════════════════════════════════

def get_inputs():
    hr('═')
    print("  Universal Audiobook File Renamer")
    hr('═')
    print()

    path = input("  Audiobook directory (Enter = current dir): ").strip() or "."
    abs_path = os.path.abspath(path)
    print(f"  → {abs_path}\n")

    while not (author := input("  Author name : ").strip()):
        print("  Cannot be empty.")
    while not (title := input("  Book title  : ").strip()):
        print("  Cannot be empty.")

    return path, sanitize(author), sanitize(title)


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main():
    try:
        directory, author, title = get_inputs()
        root = Path(directory)

        if not root.exists():
            print(f"  Error: '{directory}' does not exist.")
            return

        # ── Discover ────────────────────────────────────────
        disc_folders = collect_disc_folders(root)
        root_files   = get_audio_files(root)

        if not disc_folders and not root_files:
            print("  No audio files or disc folders found.")
            return

        # ── Interactive disc review (loop allows going back) ─
        discs, labels = disc_review(disc_folders, root)

        # ── Build full file list from confirmed disc order ───
        while True:
            all_files = root_files[:]
            for d in discs:
                all_files.extend(get_audio_files(d))

            result = file_preview(all_files, author, title, root, discs, labels)

            if result is None:
                # User pressed 'b' — go back to disc review
                discs, labels = disc_review(discs, root)
                continue

            if result:
                break   # confirmed

        # ── Rename ──────────────────────────────────────────
        rename_files(all_files, author, title, root, discs)

    except SystemExit:
        pass
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    except Exception as e:
        print(f"\n  Unexpected error: {e}")
        raise


if __name__ == "__main__":
    main()
