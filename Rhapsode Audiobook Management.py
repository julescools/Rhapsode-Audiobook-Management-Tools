#!/usr/bin/env python3
"""
Rhapsode - a launcher for the audiobook toolbox.

Sits at the root of the toolbox directory and dispatches the scripts in
./Tools/ against a target directory (defaults to the current working dir).

A rhapsode was an ancient Greek performer who recited epic poetry from
memory - the Homeric / Virgilian equivalent of an audiobook narrator.
"""

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Windows console encoding safety
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "Tools"

# Minimum terminal size we want for the menu to render comfortably.
MIN_COLS = 90
MIN_ROWS = 30

# Dim styling for secondary text (tool summaries, missing-tool lines).
USE_COLOR = sys.stdout.isatty()
DIM   = "\x1b[2m"  if USE_COLOR else ""
RESET = "\x1b[0m"  if USE_COLOR else ""


# -------------------------- tool inventory --------------------------

@dataclass
class Tool:
    name: str
    summary: str         # one-liner shown inline in the menu
    desc: str            # longer description shown in the 'i' info view
    filename: str        # exact name of the file inside Tools/
    needs_copy: bool = False   # True if the script uses __file__ to find its cwd

    @property
    def path(self):
        return TOOLS_DIR / self.filename

    @property
    def exists(self):
        return self.path.is_file()

    @property
    def is_bat(self):
        return self.filename.lower().endswith(".bat")


CATEGORIES = [
    ("Library", [
        Tool(
            name="Audiobook Library Manager",
            summary="scanner, fixer, and batch renamer for an Audiobookshelf root",
            desc="Full library tool for an Audiobookshelf root directory. "
                 "Scan cache, corrupt-file detection via ffprobe, batch "
                 "renames, fuzzy-match cleanup. The big one. Auto-prompts "
                 "to install its pip deps (mutagen, rapidfuzz, rich) on "
                 "first run.",
            filename="audiobook_manager.py",
        ),
    ]),

    ("Split & Extract", [
        Tool(
            name="Slice or extract segment",
            summary="break one audiobook file into chunks, extract by time, or split by chapter",
            desc="Takes a single audio file (mp3, m4a, m4b, flac, ogg, "
                 "opus, wav, aac, wma) and offers three modes: (1) slice "
                 "into equal-length segments with custom naming; (2) "
                 "extract a single segment by start/end time with size "
                 "estimate; (3) split by embedded chapter markers (m4b). "
                 "Uses -c copy throughout - no re-encoding, no quality loss.",
            filename="audiobook_splitter_extraction_tool.py",
        ),
        Tool(
            name="Extract m4b chapters (parallel)",
            summary="per-chapter extraction from every m4b, multi-threaded",
            desc="For each .m4b in the target directory, extract every "
                 "chapter as its own audio file. Multi-threaded with live "
                 "progress display. Chapter titles become filenames.",
            filename="extract_m4b_chapter_audio_files.py",
        ),
    ]),

    ("Join", [
        Tool(
            name="Join Libation m4b parts + cover",
            summary="merge split m4b files, preserve chapters, embed cover.jpg",
            desc="Merge all .m4b files in a directory into a single "
                 "audiobook, preserving chapter markers and embedding "
                 "cover.jpg. Intended for Libation-exported chapter files.",
            filename="join_libation_m4b_files_and_cover_dot_imgformat_"
                     "into_single_audiobook_file.py",
            needs_copy=True,
        ),
        Tool(
            name="Combine audio files (batch, Windows)",
            summary="ffmpeg concat example - edit the .bat before running",
            desc="Windows batch concat example. WARNING: currently "
                 "hardcoded to a specific trilogy - edit the .bat file "
                 "before using on other sources.",
            filename="combine_audio_ffmpeg.bat",
        ),
    ]),

    ("Organize", [
        Tool(
            name="Flatten multi-disc audiobook folders",
            summary="collapse Disc 1 / Disc 2 / CD3 ... into a flat directory",
            desc="Take Disc 1 / Disc 2 / CD3 / ... subfolders and merge "
                 "them into a single flat directory with renamed sequential "
                 "tracks. Interactive disc-order review before committing.",
            filename="audiobook_take_all_files_from_subdirectories_like_"
                     "disc_1_disc_2_etc_and_rename_and_organize_into_single_"
                     "directory_with_audio_files.py",
        ),
        Tool(
            name="Flatten multi-disc video folders",
            summary="same flattener, for video files",
            desc="Same logic as the audio flattener, but for video files.",
            filename="video_take_all_files_from_subdirectories_and_"
                     "rename_tool.py",
            needs_copy=True,
        ),
    ]),

    ("Rename", [
        Tool(
            name="PretextEdit - add prefix to filenames",
            summary="add a prefix to filenames in this folder or subfolders",
            desc="Add a prefix to filenames in the target directory, or "
                 "across multiple selected subdirectories. Live preview of "
                 "the first N renames before committing.",
            filename="PretextEdit_-_Change_beginning_of_filenames_for_root_"
                     "or_subdirectories_-_Lightweight_filename_normalization_"
                     "tool_for_media_server_command_interfaces.py",
            needs_copy=True,
        ),
        Tool(
            name="Part/Chapter/Subchapter -> sequential",
            summary="collapse nested numbering like '01 - 02 - 03' into flat '001'",
            desc="Rename files like 'Title 01 - 02 - 03.mp3' into flat "
                 "sequential 'Title - 001.mp3' numbering. Originally "
                 "written for Lolita - edit the regex inside the script "
                 "for other titles.",
            filename="rename_part_chapter_subchapter_to_simple_sequential_"
                     "numbers.py",
        ),
        Tool(
            name="Strip text before first space (Windows)",
            summary="remove everything up to and including the first space in names",
            desc="Removes everything up to and including the first space "
                 "in every filename in the target directory. Useful for "
                 "stripping leading index numbers or junk prefixes.",
            filename="remove_first_space.bat",
        ),
    ]),

    ("Metadata & Repair", [
        Tool(
            name="Clear all MP3 metadata",
            summary="strip every ID3 tag from every .mp3 in the folder",
            desc="Strips every ID3 tag from every .mp3 in the target "
                 "directory. Requires the mutagen pip package.",
            filename="clear_mp3_metadata.py",
            needs_copy=True,
        ),
        Tool(
            name="Repair MP3 (re-encode)",
            summary="re-encode every .mp3 via ffmpeg, keeps .backup copies",
            desc="Re-encodes every .mp3 in the target directory via "
                 "ffmpeg (libmp3lame, 192k, 44.1kHz) to fix header/frame "
                 "corruption. Keeps the originals alongside as .backup.",
            filename="repair_mp3.py",
        ),
    ]),
]


# -------------------------- terminal size --------------------------

def ensure_terminal_size():
    """Best-effort: request a minimum terminal size, warn if we can't.

    Uses the XTerm resize escape (CSI 8 ; rows ; cols t), which is
    respected by Windows Terminal, iTerm2, modern xterms, and most other
    VT-compatible terminals.  Falls back to Windows' `mode con:` command
    on cmd.exe.  If the final size is still too small, prints a warning
    but continues.
    """
    # Ask via ANSI escape.
    if USE_COLOR:
        sys.stdout.write(f"\x1b[8;{MIN_ROWS};{MIN_COLS}t")
        sys.stdout.flush()

    # Windows fallback for classic cmd.exe.
    if sys.platform == "win32":
        try:
            subprocess.run(
                f"mode con: cols={MIN_COLS} lines={MIN_ROWS}",
                shell=True, check=False, capture_output=True, timeout=2,
            )
        except Exception:
            pass

    # Give the terminal a beat to actually resize before re-measuring.
    time.sleep(0.05)

    size = shutil.get_terminal_size(fallback=(MIN_COLS, MIN_ROWS))
    if size.columns < MIN_COLS or size.lines < MIN_ROWS:
        print()
        print(f"  ! Terminal is {size.columns}x{size.lines}; "
              f"recommended at least {MIN_COLS}x{MIN_ROWS}.")
        print("    Please widen / enlarge the window for the menu to "
              "render cleanly.")
        time.sleep(1.2)


# -------------------------- display --------------------------

BANNER = r"""
   ___ _                            _
  / _ \ |__   __ _ _ __  ___  ___  __| | ___
 | |_) |  _ \ / _` | '_ \/ __|/ _ \/ _` |/ _ \
 |    _/ | | | (_| | |_) \__ \ (_) | (_| |  __/
 |_| |_| |_|\__,_| .__/|___/\___/ \__,_|\___|
                 |_|
     audiobook toolbox launcher
"""


def print_header():
    print(BANNER)


def print_menu(target_dir):
    """Draw the main menu. Returns the flat list of selectable tools."""
    print(f"  target dir:   {target_dir}")
    print(f"  toolbox root: {ROOT}")
    print()

    items = []        # selection index -> Tool
    missing = []      # Tools whose file isn't in Tools/
    idx = 1

    for cat_name, tools in CATEGORIES:
        available = [t for t in tools if t.exists]
        cat_missing = [t for t in tools if not t.exists]
        missing.extend(cat_missing)

        if not available:
            # Category has no available tools; it'll be referenced in
            # the "missing" summary at the bottom instead of an empty
            # header here.
            continue

        print(f"  {cat_name}")
        for tool in available:
            print(f"     {idx:>2}. {tool.name}")
            print(f"         {DIM}{tool.summary}{RESET}")
            items.append(tool)
            idx += 1
        print()

    print("  Options")
    print("      d. Change target directory")
    print("      i. Info about a tool")
    print("      s. Status (list all tools, including missing)")
    print("      q. Quit")

    if missing:
        print()
        print(f"  {DIM}! {len(missing)} tool file(s) not found in Tools/ - "
              f"press 's' for expected filenames:{RESET}")
        for tool in missing:
            print(f"     {DIM}- {tool.name}{RESET}")

    return items


def print_status():
    print()
    print(f"  Tools directory: {TOOLS_DIR}")
    print()
    for cat_name, tools in CATEGORIES:
        print(f"  {cat_name}")
        for tool in tools:
            mark = "OK  " if tool.exists else "----"
            print(f"    [{mark}]  {tool.name}")
            print(f"             expected file: {tool.filename}")
            if not tool.exists:
                print(f"             {DIM}not found at: {tool.path}{RESET}")
        print()
    input("  Press Enter to return... ")


def print_tool_info(items):
    if not items:
        print("  No tools available.")
        return
    raw = input("  Tool number for info: ").strip()
    if not raw.isdigit():
        return
    i = int(raw) - 1
    if not (0 <= i < len(items)):
        print("  Out of range.")
        return
    tool = items[i]
    print()
    print(f"  {tool.name}")
    print(f"  {'-' * len(tool.name)}")
    print(f"  {tool.desc}")
    print()
    print(f"  File:        {tool.filename}")
    print(f"  Copied to target before run: "
          f"{'yes' if tool.needs_copy else 'no'}")
    input("\n  Press Enter to return... ")


# -------------------------- running a tool --------------------------

def run_tool(tool, target_dir):
    print()
    print(f"-> {tool.name}")
    print(f"   in: {target_dir}")
    print(f"   tool: {tool.path}")
    print("-" * 60)

    if not target_dir.is_dir():
        print(f"  Target directory does not exist: {target_dir}")
        input("  Press Enter... ")
        return

    # For tools that resolve their working dir via __file__, copy the
    # script into the target dir under a tagged name, run it there,
    # then remove it.  This lets every tool operate on the user's
    # chosen dir without modifying any of the underlying scripts.
    temp_path = None
    if tool.needs_copy:
        temp_path = target_dir / f"_rhapsode_{tool.filename}"
        if temp_path.exists():
            print(f"  A previous temp copy already exists: {temp_path.name}")
            print("  Remove it and try again.")
            input("  Press Enter... ")
            return
        try:
            shutil.copy2(tool.path, temp_path)
        except Exception as e:
            print(f"  Could not copy tool into target dir: {e}")
            input("  Press Enter... ")
            return
        run_path = temp_path
    else:
        run_path = tool.path

    if tool.is_bat:
        if sys.platform != "win32":
            print("  This is a .bat file; only runnable on Windows.")
            if temp_path and temp_path.exists():
                temp_path.unlink()
            input("  Press Enter... ")
            return
        cmd = ["cmd", "/c", str(run_path)]
    else:
        cmd = [sys.executable, str(run_path)]

    try:
        subprocess.run(cmd, cwd=str(target_dir))
    except KeyboardInterrupt:
        print("\n  (Interrupted.)")
    except Exception as e:
        print(f"  Error running tool: {e}")
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError as e:
                print(f"  (Could not remove temp copy "
                      f"{temp_path.name}: {e})")

    print("-" * 60)
    input("Press Enter to return to the menu... ")


# -------------------------- target dir prompt --------------------------

def prompt_target_dir(current):
    print()
    print(f"  Current target: {current}")
    print("  Enter a new path (blank = keep current, '.' = launcher dir):")
    raw = input("  > ").strip().strip('"').strip("'")
    if not raw:
        return current
    if raw == ".":
        return ROOT
    new = Path(raw).expanduser()
    try:
        new = new.resolve()
    except OSError:
        pass
    if not new.is_dir():
        print(f"  Not a directory: {new}")
        return current
    return new


# -------------------------- main --------------------------

def main():
    ensure_terminal_size()

    if not TOOLS_DIR.is_dir():
        print(f"Tools directory not found: {TOOLS_DIR}")
        print("Create a 'Tools' folder next to rhapsode.py and place your")
        print("tool scripts inside it.")
        sys.exit(1)

    target_dir = Path.cwd().resolve()
    first = True

    while True:
        if first:
            print_header()
            first = False
        else:
            print()
        items = print_menu(target_dir)

        choice = input("\n  select: ").strip().lower()
        if not choice:
            continue
        if choice in ("q", "quit", "exit"):
            print()
            return
        if choice == "d":
            target_dir = prompt_target_dir(target_dir)
            continue
        if choice == "s":
            print_status()
            continue
        if choice == "i":
            print_tool_info(items)
            continue
        if choice.isdigit():
            n = int(choice) - 1
            if 0 <= n < len(items):
                run_tool(items[n], target_dir)
                continue
        print("  Invalid selection.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGoodbye.")