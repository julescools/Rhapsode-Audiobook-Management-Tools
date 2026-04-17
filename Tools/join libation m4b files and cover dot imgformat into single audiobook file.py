#!/usr/bin/env python3
"""
join_audiobook.py
-----------------
Place this script in the root folder containing your .m4b chapter files and
a cover.jpg.  It will merge them all into a single .m4b audiobook using FFmpeg,
preserving chapter markers and embedding the cover art.

Requirements: ffmpeg (and ffprobe) must be on your PATH.
"""

import os
import re
import subprocess
import sys
import json
import tempfile


# ── helpers ──────────────────────────────────────────────────────────────────

def check_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            print(f"[ERROR] '{tool}' not found. Make sure FFmpeg is installed and on your PATH.")
            pause_exit(1)


def natural_sort_key(s):
    """Sort filenames so that 'track 9' comes before 'track 10'."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def get_duration_ms(path):
    """Return the duration of a media file in milliseconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            os.path.abspath(path),
        ],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"]) * 1000  # ms


def build_chapters_file(m4b_files):
    """
    Build an FFmpeg metadata file that defines one chapter per input file.
    Returns the path to a temporary file that the caller must delete.
    """
    fd, meta_path = tempfile.mkstemp(suffix=".txt", prefix="ffmeta_")
    os.close(fd)

    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\n\n")
        cursor = 0.0
        for m4b in m4b_files:
            duration = get_duration_ms(m4b)
            # Derive a human-readable title from the filename
            title = os.path.splitext(os.path.basename(m4b))[0]
            # Strip everything up to and including " - NN - " to get just the chapter name
            # e.g. "Book Title [ID] - 03 - Chapter Two - Dobby's Warning" → "Chapter Two - Dobby's Warning"
            title = re.sub(r"^.+?\s*-\s*\d+\s*-\s*", "", title).strip()
            # Fallback: if nothing was stripped, just remove a bare leading "NN - "
            if not title:
                title = re.sub(r"^\d+[\s.\-–]+", "", os.path.splitext(os.path.basename(m4b))[0]).strip()

            start = int(cursor)
            end   = int(cursor + duration)
            f.write("[CHAPTER]\n")
            f.write("TIMEBASE=1/1000\n")
            f.write(f"START={start}\n")
            f.write(f"END={end}\n")
            f.write(f"title={title}\n\n")
            cursor += duration

    return meta_path


def build_concat_list(m4b_files):
    """Write an FFmpeg concat demuxer file; return its path."""
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="ffconcat_")
    os.close(fd)
    with open(list_path, "w", encoding="utf-8") as f:
        for path in m4b_files:
            # Absolute path with forward slashes for FFmpeg on Windows
            abs_path = os.path.abspath(path).replace("\\", "/")
            # FFmpeg concat format: close quote, escaped quote, reopen quote
            abs_path = abs_path.replace("'", "'\\''")
            f.write(f"file '{abs_path}'\n")
    return list_path


# ── main ─────────────────────────────────────────────────────────────────────

def pause_exit(code=0):
    """Keep the terminal window open before exiting."""
    input("\nPress Enter to exit...")
    sys.exit(code)


def main():
    check_ffmpeg()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # ── gather inputs ──────────────────────────────────────────────────────
    m4b_files = sorted(
        [f for f in os.listdir(".") if f.lower().endswith(".m4b")],
        key=natural_sort_key,
    )

    if not m4b_files:
        print("[ERROR] No .m4b files found in the current directory.")
        pause_exit(1)

    cover_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
    cover = next(
        (f for f in os.listdir(".") if os.path.splitext(f.lower())[0] == "cover"
         and os.path.splitext(f.lower())[1] in cover_extensions),
        None,
    )

    print(f"Found {len(m4b_files)} chapter file(s).")
    if cover:
        print(f"Found cover art: {cover}")
    else:
        print("[WARN] No cover.jpg found – output will have no embedded artwork.")

    # ── derive output name from the first file ─────────────────────────────
    # Expect names like:  "Book Title [ID] - 01 - Chapter Name.m4b"
    first = os.path.splitext(m4b_files[0])[0]
    # Try to strip the trailing "- NN - Chapter Name" part
    output_title = re.sub(r"\s*[-–]\s*\d+\s*[-–].*$", "", first).strip()
    # Also strip bare IDs in square brackets at the end
    output_title = re.sub(r"\s*\[[^\]]+\]\s*$", "", output_title).strip()
    if not output_title:
        output_title = "audiobook"
    output_file = output_title + ".m4b"

    if os.path.exists(output_file):
        answer = input(f"[?] '{output_file}' already exists. Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            pause_exit(0)

    print(f"\nOutput → {output_file}\n")

    # ── build helper files ─────────────────────────────────────────────────
    concat_list = build_concat_list(m4b_files)
    meta_file   = build_chapters_file(m4b_files)

    try:
        # ── step 1: concatenate all chapters ──────────────────────────────
        print("[1/2] Concatenating chapters …")
        concat_tmp = tempfile.mktemp(suffix="_concat.m4b")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_list,
                "-i", meta_file,
                "-map", "0:a",          # audio only – drop any embedded cover streams
                "-map_metadata", "1",
                "-c", "copy",
                concat_tmp,
            ],
            check=True,
        )

        # ── step 2: embed cover art (if available) ────────────────────────
        print("[2/2] Embedding cover art and finalising …")
        if cover:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", concat_tmp,
                    "-i", cover,
                    "-map", "0:a",
                    "-map", "1:v",
                    "-map_metadata", "0",
                    "-c:a", "copy",
                    "-c:v", "png",
                    "-disposition:v", "attached_pic",
                    output_file,
                ],
                check=True,
            )
        else:
            os.rename(concat_tmp, output_file)
            concat_tmp = None  # nothing to clean up

        print(f"\n✓ Done!  Saved as: {output_file}")
        pause_exit(0)

    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] FFmpeg failed (exit code {e.returncode}).")
        pause_exit(1)
    finally:
        for tmp in (concat_list, meta_file):
            try:
                os.remove(tmp)
            except OSError:
                pass
        if 'concat_tmp' in dir() and concat_tmp and os.path.exists(concat_tmp):
            try:
                os.remove(concat_tmp)
            except OSError:
                pass


if __name__ == "__main__":
    main()