#!/usr/bin/env python3
"""
audiobook_slicer.py - Slice or extract segments from audio files using ffmpeg.

Modes:
  1. Slice into equal-length segments (e.g. 10-minute chunks)
  2. Extract a single segment by start/end time
  3. Split by chapter markers (m4b etc.)

Uses -c copy throughout: no re-encoding, no quality loss, fast.
Requires ffmpeg and ffprobe on PATH.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Windows console encoding safety
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus",
              ".flac", ".wav", ".wma", ".mka"}


# -------------------------- utilities --------------------------

def check_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"Error: '{tool}' not found on PATH.")
            sys.exit(1)


def probe(path):
    """Return duration (sec), bit_rate (bps), size (bytes), format_name, chapters."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,bit_rate,size,format_name",
        "-show_chapters", "-of", "json", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(r.stdout)
    fmt = data.get("format", {})
    return {
        "duration": float(fmt.get("duration") or 0),
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "size": int(fmt.get("size") or path.stat().st_size),
        "format_name": fmt.get("format_name", ""),
        "chapters": data.get("chapters", []),
    }


def fmt_duration(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def fmt_size(b):
    b = float(b)
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def fmt_bitrate(bps):
    if bps <= 0:
        return "?"
    return f"{bps // 1000} kbps"


def sanitize_filename(name):
    """Strip characters disallowed on Windows and trailing dots/spaces."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.rstrip(". ")
    return name or "output"


# -------------------------- prompts --------------------------

def prompt_int(label, default=0):
    while True:
        s = input(f"    {label} [{default}]: ").strip()
        if not s:
            return default
        try:
            v = int(s)
            if v < 0:
                print("    Must be >= 0.")
                continue
            return v
        except ValueError:
            print("    Enter a number.")


def prompt_time(label):
    """Prompt for h / m / s. Blank = 0. Returns total seconds."""
    print(f"  {label}")
    h = prompt_int("hours",   0)
    m = prompt_int("minutes", 0)
    s = prompt_int("seconds", 0)
    total = h * 3600 + m * 60 + s
    print(f"    -> {fmt_duration(total)}")
    return total


def prompt_confirm(msg="Press Enter to execute, 'n' to cancel"):
    return input(f"{msg}: ").strip().lower() != "n"


# -------------------------- file loading --------------------------

def find_audio_file():
    """Auto-load single audio file in cwd, otherwise prompt."""
    cwd = Path.cwd()
    files = sorted(p for p in cwd.iterdir()
                   if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if len(files) == 1:
        print(f"Loaded: {files[0].name}")
        return files[0]
    if len(files) > 1:
        print("Multiple audio files found:")
        for i, f in enumerate(files, 1):
            print(f"  {i}. {f.name}")
        while True:
            s = input(f"Select [1-{len(files)}]: ").strip() or "1"
            if s.isdigit() and 1 <= int(s) <= len(files):
                return files[int(s) - 1]
            print("  Invalid selection.")
    print("No audio file in current directory.")
    while True:
        p = input("Path to audio file: ").strip().strip('"').strip("'")
        p = Path(p).expanduser()
        if p.is_file():
            return p
        print("  File not found.")


def print_source_info(src, info):
    print()
    print(f"Source:   {src.name}")
    print(f"Duration: {fmt_duration(info['duration'])}")
    print(f"Size:     {fmt_size(info['size'])}")
    print(f"Bitrate:  {fmt_bitrate(info['bit_rate'])}")
    if info["chapters"]:
        print(f"Chapters: {len(info['chapters'])}")


# -------------------------- ffmpeg operations --------------------------

def run_copy_segment(src, start, duration, out_path, quiet=False):
    """Extract [start, start+duration) with -c copy. Returns True on success."""
    cmd = [
        "ffmpeg", "-hide_banner",
        "-loglevel", "error" if quiet else "warning",
        "-ss", f"{start}",
        "-t",  f"{duration}",
        "-i",  str(src),
        "-map", "0", "-c", "copy",
        "-map_metadata", "0",
        "-y", str(out_path),
    ]
    r = subprocess.run(cmd)
    return r.returncode == 0


# -------------------------- mode: slice by duration --------------------------

def mode_slice(src, info):
    duration = info["duration"]
    print()
    print("Segment length (time per output file):")
    chunk = prompt_time("duration")
    if chunk <= 0:
        print("Segment length must be > 0.")
        return
    if chunk >= duration:
        print("Segment length is >= source duration. Nothing to slice.")
        return

    n_full = int(duration // chunk)
    remainder = duration - n_full * chunk
    n_total = n_full + (1 if remainder > 0.5 else 0)
    if n_total == n_full:
        remainder = 0

    stem = src.stem
    ext = src.suffix
    print()
    print("Output naming:")
    print(f"  1. '{stem} 01{ext}'  (default)")
    print( "  2. Custom prefix")
    ch = input("  > [1]: ").strip() or "1"
    if ch == "2":
        raw = input("  Prefix (number + extension will be appended): ").strip()
        prefix = sanitize_filename(raw) or sanitize_filename(stem)
    else:
        prefix = sanitize_filename(stem)

    pad = max(2, len(str(n_total)))
    out_dir = src.parent / f"{prefix} (sliced)"

    first_name = f"{prefix} {'1'.zfill(pad)}{ext}"
    last_name  = f"{prefix} {str(n_total).zfill(pad)}{ext}"
    avg_size = info["size"] / n_total if n_total else 0

    print()
    print("Preview:")
    print(f"  Output dir:     {out_dir}")
    print(f"  File count:     {n_total}")
    print(f"  Segment length: {fmt_duration(chunk)}")
    print(f"  First file:     {first_name}")
    if n_total >= 2:
        tail = f" (last segment: {fmt_duration(remainder)})" if remainder > 0 else ""
        print(f"  Last file:      {last_name}{tail}")
    print(f"  Approx size/file: {fmt_size(avg_size)}")
    print()
    if not prompt_confirm():
        print("Cancelled.")
        return

    out_dir.mkdir(exist_ok=True)
    print()
    errors = 0
    for i in range(n_total):
        start = i * chunk
        seg_len = chunk if i < n_full else remainder
        out_name = f"{prefix} {str(i + 1).zfill(pad)}{ext}"
        out_path = out_dir / out_name
        print(f"  [{i+1:>{len(str(n_total))}}/{n_total}] {out_name}")
        if not run_copy_segment(src, start, seg_len, out_path, quiet=True):
            print("      ! ffmpeg error")
            errors += 1
    print()
    if errors:
        print(f"Done with {errors} error(s). Output: {out_dir}")
    else:
        print(f"Done. {n_total} files written to: {out_dir}")


# -------------------------- mode: extract segment --------------------------

def mode_extract(src, info):
    duration = info["duration"]
    print()
    start = prompt_time("Start time:")
    if start >= duration:
        print(f"Start ({fmt_duration(start)}) is past end of file ({fmt_duration(duration)}).")
        return
    end = prompt_time("End time:")
    if end <= start:
        print("End must be after start.")
        return
    if end > duration:
        print(f"End clamped to source duration: {fmt_duration(duration)}")
        end = duration

    seg_dur = end - start
    est_size = info["size"] * (seg_dur / duration) if duration else 0

    # Default output name: "<stem> [HH.MM.SS-HH.MM.SS]<ext>"
    s_tag = fmt_duration(start).replace(":", ".")
    e_tag = fmt_duration(end).replace(":", ".")
    default_name = sanitize_filename(f"{src.stem} [{s_tag}-{e_tag}]{src.suffix}")

    print()
    print("Extraction summary:")
    print(f"  From:        {fmt_duration(start)}")
    print(f"  To:          {fmt_duration(end)}")
    print(f"  Duration:    {fmt_duration(seg_dur)}")
    print(f"  Approx size: {fmt_size(est_size)}")
    print()
    raw = input(f"Output filename [{default_name}]: ").strip()
    out_name = raw or default_name
    if not out_name.lower().endswith(src.suffix.lower()):
        out_name += src.suffix
    out_path = src.parent / sanitize_filename(out_name)
    print(f"  -> {out_path}")
    print()
    if not prompt_confirm():
        print("Cancelled.")
        return

    print("Running ffmpeg...")
    if run_copy_segment(src, start, seg_dur, out_path, quiet=False):
        print(f"Done: {out_path}")
    else:
        print("ffmpeg reported an error.")


# -------------------------- mode: split by chapters --------------------------

def mode_chapters(src, info):
    chapters = info["chapters"]
    print()
    if not chapters:
        print("No chapter markers found in this file.")
        return

    print(f"Found {len(chapters)} chapters:")
    for i, ch in enumerate(chapters, 1):
        title = ch.get("tags", {}).get("title", f"Chapter {i}")
        s = float(ch.get("start_time", 0))
        e = float(ch.get("end_time", 0))
        print(f"  {i:>3}. [{fmt_duration(s)} - {fmt_duration(e)}] {title}")
    print()
    if input("Split into one file per chapter? (y/N): ").strip().lower() != "y":
        return

    pad = max(2, len(str(len(chapters))))
    out_dir = src.parent / f"{src.stem} (chapters)"
    out_dir.mkdir(exist_ok=True)

    errors = 0
    for i, ch in enumerate(chapters, 1):
        title = ch.get("tags", {}).get("title", f"Chapter {i}")
        s = float(ch.get("start_time", 0))
        e = float(ch.get("end_time", 0))
        seg_dur = e - s
        if seg_dur <= 0:
            continue
        safe_title = sanitize_filename(title)
        out_name = f"{str(i).zfill(pad)} - {safe_title}{src.suffix}"
        out_path = out_dir / out_name
        print(f"  [{i:>{len(str(len(chapters)))}}/{len(chapters)}] {out_name}")
        if not run_copy_segment(src, s, seg_dur, out_path, quiet=True):
            print("      ! ffmpeg error")
            errors += 1
    print()
    if errors:
        print(f"Done with {errors} error(s). Output: {out_dir}")
    else:
        print(f"Done. Chapters written to: {out_dir}")


# -------------------------- main menu --------------------------

MODES = [
    ("slice",    "Slice into equal-length segments"),
    ("extract",  "Extract a single segment"),
    ("chapters", "Split by chapter markers"),
    ("reload",   "Load a different file"),
    ("quit",     "Quit"),
]


def main_menu():
    print()
    print("What would you like to do?")
    for i, (_, desc) in enumerate(MODES, 1):
        print(f"  {i}. {desc}")
    while True:
        s = input("  > [1]: ").strip() or "1"
        if s.isdigit() and 1 <= int(s) <= len(MODES):
            return MODES[int(s) - 1][0]
        print("  Invalid choice.")


def main():
    check_ffmpeg()
    print("=" * 52)
    print(" Audiobook Slicer")
    print("=" * 52)

    src = find_audio_file()
    info = probe(src)
    print_source_info(src, info)

    while True:
        choice = main_menu()
        if choice == "slice":
            mode_slice(src, info)
        elif choice == "extract":
            mode_extract(src, info)
        elif choice == "chapters":
            mode_chapters(src, info)
        elif choice == "reload":
            src = find_audio_file()
            info = probe(src)
            print_source_info(src, info)
        elif choice == "quit":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)