#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║              Audiobook Library Manager  v2.0                 ║
║  Run from your audiobook root directory.                     ║
║                                                              ║
║  Python deps (auto-install offered at startup):              ║
║    pip install mutagen rapidfuzz rich                        ║
║                                                              ║
║  External tool (optional, highly recommended):               ║
║    FFmpeg  →  https://ffmpeg.org/download.html               ║
║    macOS:   brew install ffmpeg                              ║
║    Ubuntu:  sudo apt install ffmpeg                          ║
║    Windows: winget install ffmpeg                            ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import re
import sys
import html
import json
import shutil
import select
import hashlib
import difflib
import platform
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from collections import defaultdict, deque
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional, Callable, Any

ROOT = Path.cwd()

# Use all available CPU cores for the thread pool
NUM_WORKERS = os.cpu_count() or 1

AUDIO_EXTS = {'.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus',
              '.wav', '.aac', '.wma', '.aiff', '.ape', '.mp4'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
JUNK_EXTS  = {'.txt', '.nfo', '.url', '.html', '.htm', '.sfv',
              '.md5', '.log', '.db', '.ini'}

CACHE_FILE = ROOT / '.audiobook_cache.json'
LOG_FILE   = ROOT / '.audiobook_scan.log'

# Patterns that explicitly indicate a sub-folder is a disc/part/cd split.
# These must appear IN ADDITION to (or instead of) similarity checks.
# Deliberately excludes 'chapter' — chapter sub-folders are almost always
# separate books in a series, not disc splits of a single audiobook.
DISC_PATTERNS = re.compile(
    r'\b(?:disc|disk|cd)\s*\.?\s*\d+'           # disc 1, CD2, disk.3
    r'|\bpart\s*\.?\s*\d+\b'                    # part 1, part.2  (requires word boundary)
    r'|\bvol(?:ume)?\s*\.?\s*\d+\b'             # vol 1, volume 2
    r'|\bd\s*\d+\b'                             # D1, D2 (abbreviated disc)
    r'|\bpt\s*\.?\s*\d+\b',                     # pt1, pt.2
    re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
#  Dependency management
# ─────────────────────────────────────────────────────────────────────────────

def _try_import(module):
    try:
        return __import__(module)
    except ImportError:
        return None

def _pip_install(pkg):
    try:
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', pkg, '-q'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except Exception:
        return False

def _offer_install(pkg, imp=None):
    imp = imp or pkg
    ans = input(f"\n  Package '{pkg}' not found. Install now? (y/n): ").strip().lower()
    if ans == 'y':
        print(f"  Installing {pkg}...", end=' ', flush=True)
        if _pip_install(pkg):
            print("done.")
            return _try_import(imp)
        else:
            print(f"failed. Run: pip install {pkg}")
    return None


# ── Load optional Python dependencies ────────────────────────────────────────

_rich_mod   = _try_import('rich')    or _offer_install('rich')
HAS_RICH    = _rich_mod is not None

_mut_mod    = _try_import('mutagen') or _offer_install('mutagen')
HAS_MUTAGEN = _mut_mod is not None

_fuz_mod    = _try_import('rapidfuzz') or _offer_install('rapidfuzz')
HAS_FUZZY   = _fuz_mod is not None

if HAS_RICH:
    from rich.console  import Console
    from rich.console  import Group  as RGroup
    from rich.table    import Table
    from rich.panel    import Panel
    from rich.rule     import Rule
    from rich.prompt   import Prompt, Confirm, IntPrompt
    from rich.live     import Live
    from rich.text     import Text
    from rich.progress import (Progress, SpinnerColumn,
                                BarColumn, TextColumn, MofNCompleteColumn)
    console = Console()

if HAS_FUZZY:
    from rapidfuzz import fuzz as rfuzz


# ── FFmpeg detection & audio probing ─────────────────────────────────────────

HAS_FFMPEG = shutil.which('ffmpeg') is not None

def _ffmpeg_startup_notice():
    if HAS_FFMPEG:
        good("FFmpeg detected — full audio analysis available.")
    else:
        warn("FFmpeg not found. Audio quality analysis will be limited.")
        _sys = platform.system()
        hints = {
            'Darwin':  "  Install: brew install ffmpeg",
            'Linux':   "  Install: sudo apt install ffmpeg",
            'Windows': "  Install: winget install ffmpeg  (or https://ffmpeg.org)",
        }
        dim(hints.get(_sys, "  Download: https://ffmpeg.org/download.html"))

# ASCII spinner frames — cycles at ~12 fps driven by monotonic time
_SPIN = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']


FFPROBE_TIMEOUT = 8   # seconds

def ffprobe_info(filepath):
    """
    Return audio info dict via ffprobe, or None on failure.
    Uses Popen + communicate so we can guarantee kill() on timeout —
    subprocess.check_output does NOT reliably kill on Windows.
    Returns 'TIMEOUT' sentinel on timeout so callers can distinguish.
    """
    if not HAS_FFMPEG:
        return None
    cmd = ['ffprobe', '-v', 'error', '-print_format', 'json',
           '-show_streams', '-show_format', str(filepath)]
    proc = None
    try:
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        try:
            out, err = proc.communicate(timeout=FFPROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return 'TIMEOUT'

        if proc.returncode != 0:
            # Return stderr text as a special dict so callers get the real error
            err_text = err.decode('utf-8', errors='replace').strip()
            return {'_error': err_text or f'ffprobe exited with code {proc.returncode}'}

        data   = json.loads(out)
        result = {}
        for s in data.get('streams', []):
            if s.get('codec_type') == 'audio':
                result['codec']       = s.get('codec_name', '').upper()
                result['sample_rate'] = int(s.get('sample_rate', 0)) or None
                result['channels']    = s.get('channels')
                bd = s.get('bits_per_raw_sample') or s.get('bits_per_sample')
                result['bit_depth']   = int(bd) if bd else None
                br = s.get('bit_rate')
                if br:
                    result['bitrate'] = int(br) // 1000
                break
        fmt = data.get('format', {})
        result['duration'] = float(fmt.get('duration') or 0) or None
        result['format']   = result.get('codec') or Path(filepath).suffix.lstrip('.').upper()
        if not result.get('bitrate'):
            br = fmt.get('bit_rate')
            if br:
                result['bitrate'] = int(br) // 1000
        return result or None
    except Exception:
        if proc and proc.poll() is None:
            try: proc.kill(); proc.communicate()
            except Exception: pass
        return None


def ffprobe_diagnose(filepath: Path) -> dict:
    """
    Run a thorough diagnostic on a single file and return a structured result:
      {
        'readable':   bool,
        'duration':   float | None,
        'codec':      str | None,
        'bitrate':    int | None,
        'error':      str | None,   # raw ffprobe error text
        'timeout':    bool,
        'zero_bytes': bool,
        'truncated':  bool,         # readable but suspiciously short
        'salvageable':bool,         # ffmpeg error-tolerant copy got *something*
        'repair_options': [str],    # which repair methods are worth trying
      }
    """
    result = {
        'readable': False, 'duration': None, 'codec': None, 'bitrate': None,
        'error': None, 'timeout': False, 'zero_bytes': False,
        'truncated': False, 'salvageable': False, 'repair_options': [],
    }

    if not filepath.exists():
        result['error'] = 'File not found'
        return result

    size = filepath.stat().st_size
    if size == 0:
        result['zero_bytes'] = True
        result['error'] = 'File is completely empty (0 bytes)'
        return result

    if not HAS_FFMPEG:
        result['error'] = 'ffmpeg not available — cannot diagnose'
        return result

    # ── Pass 1: standard probe ────────────────────────────────────────────────
    info = ffprobe_info(filepath)
    if info == 'TIMEOUT':
        result['timeout'] = True
        result['error']   = (f'ffprobe did not respond within {FFPROBE_TIMEOUT}s. '
                             f'The file may be on a slow/stalled network share, '
                             f'or may require a codec that hangs during detection.')
        return result

    if isinstance(info, dict) and '_error' in info:
        # ffprobe embeds the full file path at the start of error lines.
        # Strip it so we show just the actual error, not a wall of path text.
        raw_err = info['_error'] or 'ffprobe could not read file'
        fp_str  = str(filepath)
        # Remove lines that are just the filepath + colon prefix
        clean_lines = []
        for line in raw_err.splitlines():
            stripped = line.strip()
            if stripped.startswith(fp_str):
                stripped = stripped[len(fp_str):].lstrip(':').strip()
            if stripped:
                clean_lines.append(stripped)
        result['error'] = '  |  '.join(clean_lines) if clean_lines else raw_err
    elif isinstance(info, dict):
        result['readable'] = True
        result['duration'] = info.get('duration')
        result['codec']    = info.get('codec')
        result['bitrate']  = info.get('bitrate')
        if result['duration'] is not None and result['duration'] < MIN_DURATION_S:
            result['truncated'] = True

    # ── Pass 2: error-tolerant probe to check salvageability ──────────────────
    # Even if the file failed standard probing, ffmpeg may be able to extract
    # something useful with -err_detect ignore_err.
    try:
        cmd = ['ffprobe', '-v', 'error',
               '-err_detect', 'ignore_err',
               '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1',
               str(filepath)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate(timeout=FFPROBE_TIMEOUT)
        raw = out.decode().strip()
        if raw and raw != 'N/A':
            try:
                dur = float(raw)
                result['salvageable'] = dur > 0.5
                if result['duration'] is None:
                    result['duration'] = dur
            except ValueError:
                pass
    except Exception:
        pass

    # ── Determine which repair options make sense ─────────────────────────────
    # For any non-empty, non-timeout file we always offer all three repair
    # methods. A 183MB m4a with a broken moov atom is not "salvageable" by
    # the tolerant probe, but reencode + format-forcing will often recover it.
    # Better to give the user the option than silently hide it.
    opts = []
    if not result['zero_bytes'] and not result['timeout']:
        opts.append('remux')
        opts.append('tolerant_copy')
        opts.append('reencode')
    opts.append('delete')
    result['repair_options'] = opts
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Console helpers
# ─────────────────────────────────────────────────────────────────────────────

def banner(text):
    if HAS_RICH:
        console.print(Rule(f"[bold cyan] {text} [/bold cyan]", style="cyan"))
    else:
        print(f"\n{'─'*62}\n  {text}\n{'─'*62}")

def info(text):
    if HAS_RICH: console.print(f"  [cyan]→[/cyan] {text}")
    else:        print(f"  → {text}")

def warn(text):
    if HAS_RICH: console.print(f"  [yellow]⚠[/yellow]  {text}")
    else:        print(f"  ⚠  {text}")

def good(text):
    if HAS_RICH: console.print(f"  [green]✓[/green]  {text}")
    else:        print(f"  ✓  {text}")

def bad(text):
    if HAS_RICH: console.print(f"  [red]✗[/red]  {text}")
    else:        print(f"  ✗  {text}")

def dim(text):
    if HAS_RICH: console.print(f"  [dim]{text}[/dim]")
    else:        print(f"  {text}")

def h2(text):
    if HAS_RICH: console.print(f"\n  [bold yellow]{text}[/bold yellow]")
    else:        print(f"\n  {text}")

def step_header(current, total, title, severity="info"):
    color = {"error": "red", "warning": "yellow", "info": "cyan"}.get(severity, "cyan")
    if HAS_RICH:
        console.print()
        console.print(Panel(
            f"[bold white]{title}[/bold white]",
            title=f"[bold {color}] Issue {current} of {total} [/bold {color}]",
            border_style=color, padding=(0, 2)
        ))
    else:
        print(f"\n{'═'*62}")
        print(f"  Issue {current} of {total}  [{severity.upper()}]  —  {title}")
        print(f"{'═'*62}")

def prompt_confirm(msg, default=False):
    if HAS_RICH:
        return Confirm.ask(f"  {msg}", default=default)
    sfx = " [Y/n]" if default else " [y/N]"
    r = input(f"  {msg}{sfx}: ").strip().lower()
    return (r in ('y', 'yes')) if r else default

def prompt_text(msg, default=""):
    if HAS_RICH:
        return (Prompt.ask(f"  {msg}", default=default)
                if default else Prompt.ask(f"  {msg}"))
    r = input(f"  {msg}" + (f" [{default}]: " if default else ": ")).strip()
    return r or default

def prompt_int(msg, default=None):
    if HAS_RICH:
        return (IntPrompt.ask(f"  {msg}", default=default)
                if default is not None else IntPrompt.ask(f"  {msg}"))
    while True:
        sfx = f" [{default}]: " if default is not None else ": "
        r = input(f"  {msg}{sfx}").strip()
        if not r and default is not None:
            return default
        try:
            return int(r)
        except ValueError:
            print("  Please enter a valid number.")

def print_table(headers, rows, title=None):
    if not rows:
        info("(no results)")
        return
    if HAS_RICH:
        t = Table(title=title, show_header=True,
                  header_style="bold magenta", border_style="dim")
        for h in headers:
            t.add_column(h)
        for row in rows:
            t.add_row(*[str(x) for x in row])
        console.print(t)
    else:
        if title:
            print(f"\n  ── {title} ──")
        widths = [
            max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
            for i, h in enumerate(headers)
        ]
        fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
        print(fmt.format(*headers))
        print("  " + "  ".join("─" * w for w in widths))
        for row in rows:
            print(fmt.format(*[str(x) for x in row]))


# ─────────────────────────────────────────────────────────────────────────────
#  Shared utility
# ─────────────────────────────────────────────────────────────────────────────

def fmt_size(n):
    for unit in ['B','KB','MB','GB','TB']:
        if n < 1024: return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

def fmt_duration(seconds):
    if seconds is None: return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s   = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"

def natural_sort_key(path):
    return [int(c) if c.isdigit() else c.lower()
            for c in re.split(r'(\d+)', path.name)]

def sanitize_filename(text):
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def find_book_dirs():
    """Directories that directly contain audio files (does not recurse into them)."""
    book_dirs = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        p = Path(dirpath)
        if p == ROOT:
            continue
        if any(Path(f).suffix.lower() in AUDIO_EXTS for f in filenames):
            book_dirs.append(p)
            dirnames.clear()
    return sorted(book_dirs)

def get_audio_files(directory):
    files = [f for f in Path(directory).iterdir()
             if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
    files.sort(key=natural_sort_key)
    return files

def get_cover_file(directory):
    for f in Path(directory).iterdir():
        if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
            return f
    return None

def get_audio_info(filepath):
    """Try ffprobe first, fall back to mutagen."""
    d = ffprobe_info(filepath)
    if d:
        return d
    if not HAS_MUTAGEN:
        return None
    try:
        from mutagen import File
        f = File(filepath, easy=False)
        if f is None: return None
        fi = getattr(f, 'info', None)
        result = {
            'format':      filepath.suffix.lstrip('.').upper(),
            'bitrate': None, 'sample_rate': None,
            'bit_depth': None, 'channels': None, 'duration': None,
        }
        if fi:
            raw_br = getattr(fi, 'bitrate', None)
            result.update({
                'duration':    getattr(fi, 'length', None),
                'sample_rate': getattr(fi, 'sample_rate', None),
                'bit_depth':   getattr(fi, 'bits_per_sample', None),
                'channels':    getattr(fi, 'channels', None),
                'bitrate':     (raw_br // 1000) if raw_br else None,
            })
        return result
    except Exception:
        return None

def quality_tier(audio_info):
    if not audio_info: return "Unknown", "white"
    fmt = (audio_info.get('format') or '').upper()
    br  = audio_info.get('bitrate')  or 0
    bd  = audio_info.get('bit_depth') or 16
    sr  = audio_info.get('sample_rate') or 44100
    lossless = fmt in ('FLAC','WAV','AIFF','APE','WV','ALAC','WMA','WAVPACK')
    if lossless and (bd >= 24 or sr > 48000): return "Maximum (Lossless Hi-Res)", "bright_cyan"
    if lossless:                               return "Audiophile (Lossless CD)",  "cyan"
    if br >= 256:                              return "High (Lossy 256+ kbps)",    "green"
    if br >= 128:                              return "Medium (Lossy 128–255 kbps)","yellow"
    if br > 0:                                 return "Low (Lossy <128 kbps)",     "red"
    return "Unknown", "white"

def dir_summary(directory):
    files = get_audio_files(directory)
    if not files: return None, "white", {}
    infos = [i for i in (get_audio_info(f) for f in files[:3]) if i]
    rep   = infos[0] if infos else None
    tier, color = quality_tier(rep)
    total_dur = sum(
        (ai.get('duration') or 0)
        for f in files
        for ai in [get_audio_info(f)]
        if ai
    )
    summary = {
        'format':      rep.get('format','?')       if rep else files[0].suffix.lstrip('.').upper(),
        'bitrate':     rep.get('bitrate')           if rep else None,
        'sample_rate': rep.get('sample_rate')       if rep else None,
        'bit_depth':   rep.get('bit_depth')         if rep else None,
        'channels':    rep.get('channels')          if rep else None,
        'duration':    total_dur or None,
        'tier':        tier,
        'file_count':  len(files),
        'total_size':  sum(f.stat().st_size for f in files),
    }
    return tier, color, summary

def normalize_name(name):
    n = name.lower()
    n = re.sub(r'[\(\[\{][^\)\]\}]*[\)\]\}]', ' ', n)
    n = re.sub(r'\b(the|a|an|audiobook|unabridged|abridged|'
               r'part|pt|vol|volume|book|cd|disc|chapter|complete)\b', ' ', n)
    n = re.sub(r'[^a-z0-9 ]', ' ', n)
    return re.sub(r'\s+', ' ', n).strip()

def fuzzy_score(a, b):
    if HAS_FUZZY:
        return rfuzz.token_sort_ratio(a, b)
    return int(difflib.SequenceMatcher(None, a, b).ratio() * 100)

def _extract_parts(name):
    year_m = re.search(r'\b(19|20)\d{2}\b', name)
    year   = year_m.group(0) if year_m else ''
    clean  = re.sub(r'[\(\[\{][^\)\]\}]*[\)\]\}]', '', name).strip(' -_')
    if ' - ' in clean:
        left, right = [p.strip() for p in clean.split(' - ', 1)]
        return left, right, year
    return '', clean, year


# ─────────────────────────────────────────────────────────────────────────────
#  Issue dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Issue:
    category:   str
    summary:    str
    detail:     str
    path:       Optional[Path]
    severity:   str                          # "info" | "warning" | "error"
    resolve_fn: Callable[['Issue'], bool]
    extra:      Any = field(default=None)





# ─────────────────────────────────────────────────────────────────────────────
#  Feature 1 — Clean Junk Files
# ─────────────────────────────────────────────────────────────────────────────

def clean_junk_files():
    banner("Clean Junk Files")
    info(f"Scanning {ROOT} …")

    by_ext = defaultdict(list)
    for dirpath, _, filenames in os.walk(ROOT):
        for fn in filenames:
            fp  = Path(dirpath) / fn
            ext = fp.suffix.lower()
            if ext in JUNK_EXTS or (fn.startswith('.') and fn not in ('.', '..')):
                by_ext[ext or fn].append(fp)

    if not by_ext:
        good("No junk files found.")
        return

    rows = [(ext, len(fps), fmt_size(sum(f.stat().st_size for f in fps)))
            for ext, fps in sorted(by_ext.items())]
    print_table(["Extension","Count","Total Size"], rows, title="Junk Files Found")
    print()

    to_delete = []
    for ext, files in sorted(by_ext.items()):
        sz = fmt_size(sum(f.stat().st_size for f in files))
        h2(f"{ext or 'hidden'}  —  {len(files)} file(s)  ({sz})")
        for f in files[:15]:
            dim(f"    {f.relative_to(ROOT)}")
        if len(files) > 15:
            dim(f"    … and {len(files)-15} more")
        if prompt_confirm(f"Select all {ext} files for deletion?"):
            to_delete.extend(files)

    if not to_delete:
        info("Nothing selected.")
        return

    warn(f"About to permanently delete {len(to_delete)} file(s).")
    if not prompt_confirm("Confirm deletion?"):
        info("Cancelled.")
        return

    deleted = 0
    for f in to_delete:
        try:
            f.unlink()
            deleted += 1
        except Exception as e:
            bad(f"Could not delete {f.name}: {e}")
    good(f"Deleted {deleted} / {len(to_delete)} file(s).")


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 2 — Duplicate Detection & Removal
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_duplicate_group(group, scores):
    dir_infos = []
    for d in group:
        tier, color, summary = dir_summary(d)
        dir_infos.append({'path': d, 'tier': tier or 'Unknown', 'summary': summary})

    rows = []
    for idx, di in enumerate(dir_infos, 1):
        s = di['summary']
        rows.append((
            str(idx), di['path'].name,
            s.get('format','?'),
            f"{s['bitrate']} kbps"   if s.get('bitrate')      else '?',
            f"{s['sample_rate']} Hz" if s.get('sample_rate')  else '?',
            f"{s['bit_depth']}-bit"  if s.get('bit_depth')    else '?',
            fmt_duration(s.get('duration')),
            fmt_size(s.get('total_size',0)),
            str(s.get('file_count','?')),
            "✓" if get_cover_file(di['path']) else "✗",
            f"{scores.get(id(di['path']),100)}%",
            di['tier'],
        ))

    print_table(["#","Directory","Fmt","Bitrate","Sample Rate","Bit Depth",
                 "Duration","Size","Files","Cover","Match","Quality Tier"], rows)

    if HAS_MUTAGEN:
        for idx, di in enumerate(dir_infos, 1):
            afiles = get_audio_files(di['path'])
            if afiles:
                try:
                    from mutagen import File as MF
                    mf = MF(afiles[0], easy=True)
                    if mf:
                        tags = [f"{t}={mf[t][0]}" for t in ('title','artist','album','date')
                                if t in mf]
                        if tags: dim(f"    [{idx}] {', '.join(tags[:4])}")
                except Exception:
                    pass

    print()
    info("Enter numbers to DELETE (e.g. '1 3'), 'k' = keep all, 's' = skip")
    choice = prompt_text("Selection", default="s")

    if choice.lower() in ('s','skip','','k','keep'):
        info("Skipped.")
        return

    for idx in [int(x) for x in choice.split() if x.isdigit()]:
        if 1 <= idx <= len(dir_infos):
            d  = dir_infos[idx-1]['path']
            sz = fmt_size(sum(f.stat().st_size for f in d.rglob('*') if f.is_file()))
            warn(f"About to permanently delete: {d.name}  ({sz})")
            if prompt_confirm(f"Confirm delete '{d.name}'?"):
                try:
                    shutil.rmtree(d)
                    good(f"Deleted: {d.name}")
                except Exception as e:
                    bad(f"Failed: {e}")
        else:
            warn(f"Index {idx} out of range.")

def find_duplicates():
    banner("Duplicate Detection & Removal")
    threshold = prompt_int("Similarity threshold % (recommended 70)", default=70)
    book_dirs = find_book_dirs()
    if not book_dirs:
        warn("No audiobook directories found.")
        return

    names    = [(d, normalize_name(d.name)) for d in book_dirs]
    groups   = []
    row_args = [(i, d, n, names, threshold) for i, (d, n) in enumerate(names)]

    info(f"Comparing {len(book_dirs)} directories "
         f"({len(book_dirs)*(len(book_dirs)-1)//2} pairs) "
         f"across {NUM_WORKERS} worker(s) …")

    if HAS_RICH:
        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                      BarColumn(), MofNCompleteColumn(), console=console) as progress:
            task     = progress.add_task("Comparing …", total=len(names))
            all_rows = [None] * len(names)
            with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
                fut_map = {pool.submit(_fuzzy_row, a): a[0] for a in row_args}
                for fut in as_completed(fut_map):
                    idx = fut_map[fut]
                    all_rows[idx] = fut.result()
                    progress.advance(task)
    else:
        print("  Running …", end=' ', flush=True)
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
            all_rows = list(pool.map(_fuzzy_row, row_args))
        print("done.")

    # Deduplicate groups in the main thread
    used: set = set()
    for row in all_rows:
        i, cluster, scores = row
        dir_i = names[i][0]
        if id(dir_i) in used: continue
        cluster = [d for d in cluster if id(d) not in used]
        if len(cluster) < 2: continue
        for d in cluster: used.add(id(d))
        groups.append((cluster, scores))

    if not groups:
        good(f"No duplicates at {threshold}% threshold.")
        return

    info(f"Found {len(groups)} duplicate group(s).\n")
    for gi, (group, scores) in enumerate(groups, 1):
        h2(f"── Group {gi} of {len(groups)} ──")
        _resolve_duplicate_group(group, scores)


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 3 — Folder Rename / Standardization
# ─────────────────────────────────────────────────────────────────────────────

FORMAT_OPTIONS = {
    '1': ('Author - Title',        '{author} - {title}'),
    '2': ('Title - Author',        '{title} - {author}'),
    '3': ('Title (Year)',           '{title} ({year})'),
    '4': ('Author - Title (Year)',  '{author} - {title} ({year})'),
    '5': ('Author/Title (nested)',  '{author}/{title}'),
    '6': ('Title only',             '{title}'),
}

def _apply_format(pattern, author, title, year):
    name = pattern.format(author=author, title=title, year=year)
    name = re.sub(r'\(\)', '', name)
    name = re.sub(r'\s{2,}', ' ', name)
    name = re.sub(r'^\s*[-–]\s*|\s*[-–]\s*$', '', name).strip()
    return sanitize_filename(name)

def standardize_folders():
    banner("Folder Rename / Standardization")
    book_dirs = find_book_dirs()
    if not book_dirs:
        warn("No audiobook directories found.")
        return

    print()
    info("Choose a target naming format:")
    for k, (label, pattern) in FORMAT_OPTIONS.items():
        print(f"    [{k}] {label:<26}  →  {pattern}")
    print("    [0] Cancel\n")

    choice = prompt_text("Format number", default="0")
    if choice == '0' or choice not in FORMAT_OPTIONS:
        info("Cancelled.")
        return

    label, pattern = FORMAT_OPTIONS[choice]
    nested = '{author}/' in pattern

    renames, skipped = [], []
    for d in book_dirs:
        author, title, year = _extract_parts(d.name)
        if not title: title = d.name
        if nested:
            if not author:
                skipped.append((d, "cannot extract author"))
                continue
            new_path = d.parent / sanitize_filename(author) / sanitize_filename(title)
        else:
            new_name = _apply_format(pattern, author, title, year)
            if not new_name or new_name == d.name: continue
            new_path = d.parent / new_name
        if new_path != d:
            renames.append((d, new_path))

    if skipped:
        warn(f"Skipping {len(skipped)} director(ies) (could not extract author).")

    if not renames:
        good("All directories already match the target format.")
        return

    print_table(["Current Name","New Name"],
                [(d.name, np.name) for d, np in renames[:40]],
                title=f"Preview — {len(renames)} rename(s)")
    if len(renames) > 40:
        dim(f"  … and {len(renames)-40} more")

    if not prompt_confirm(f"Apply all {len(renames)} rename(s)?"):
        info("Cancelled.")
        return

    ok = 0
    for old, new in renames:
        if new.exists():
            warn(f"Skip (target exists): {new.name}")
            continue
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            ok += 1
        except Exception as e:
            bad(f"Failed '{old.name}': {e}")
    good(f"Renamed {ok} / {len(renames)} director(ies).")


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 4 — Library Health HTML Report
# ─────────────────────────────────────────────────────────────────────────────

_TIER_CSS = {
    'maximum':    ('tier-max',        '#0e7490','#cffafe'),
    'audiophile': ('tier-audiophile', '#0c4a6e','#bae6fd'),
    'high':       ('tier-high',       '#14532d','#bbf7d0'),
    'medium':     ('tier-medium',     '#713f12','#fef3c7'),
    'low':        ('tier-low',        '#7f1d1d','#fee2e2'),
    'unknown':    ('tier-unknown',    '#374151','#d1d5db'),
}

def _tier_css(tier_label):
    tl = (tier_label or '').lower()
    for key, (cls,*_) in _TIER_CSS.items():
        if key in tl: return cls
    return _TIER_CSS['unknown'][0]

def _analyse_dir(directory):
    audio_files = get_audio_files(directory)
    raw_size    = sum(f.stat().st_size for f in directory.rglob('*') if f.is_file())
    cover       = get_cover_file(directory)
    tier, _, summary = dir_summary(directory)
    issues = []
    if not cover:         issues.append("No cover art")
    if not audio_files:   issues.append("No audio files")
    br = summary.get('bitrate')
    if br and br < 64:    issues.append(f"Very low bitrate ({br} kbps)")
    return {
        'name':        directory.name,
        'rel_path':    str(directory.relative_to(ROOT)),
        'format':      summary.get('format','?'),
        'bitrate':     summary.get('bitrate'),
        'sample_rate': summary.get('sample_rate'),
        'bit_depth':   summary.get('bit_depth'),
        'duration_fmt':fmt_duration(summary.get('duration')),
        'size_fmt':    fmt_size(raw_size),
        'raw_size':    raw_size,
        'file_count':  len(audio_files),
        'has_cover':   cover is not None,
        'tier':        tier or 'Unknown',
        'issues':      issues,
    }

def generate_health_report():
    banner("Library Health HTML Report")
    book_dirs = find_book_dirs()
    if not book_dirs:
        warn("No audiobook directories found.")
        return

    info(f"Scanning {len(book_dirs)} directories across {NUM_WORKERS} worker(s) …")
    data, total_size, issues_count = [], 0, 0

    if HAS_RICH:
        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                      BarColumn(), MofNCompleteColumn(), console=console) as prog:
            task = prog.add_task("Analysing …", total=len(book_dirs))
            with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
                for d, row in zip(book_dirs, pool.map(_analyse_dir, book_dirs)):
                    prog.update(task, advance=1, description=d.name[:45])
                    data.append(row)
                    total_size += row['raw_size']
                    if row['issues']: issues_count += 1
    else:
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
            for i, (d, row) in enumerate(zip(book_dirs,
                                             pool.map(_analyse_dir, book_dirs))):
                if i % 20 == 0: print(f"  {i}/{len(book_dirs)} …", end='\r')
                data.append(row)
                total_size += row['raw_size']
                if row['issues']: issues_count += 1
        print()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tier_styles = "\n".join(
        f"  .{cls}{{background:{bg};color:{fg};}}"
        for cls,(bg,fg) in {k:(v[1],v[2]) for k,v in _TIER_CSS.items()}.items()
    )
    # rebuild tier_styles properly
    tier_styles = "\n".join(
        f"  .{cls} {{ background:{bg}; color:{fg}; }}"
        for cls,bg,fg in _TIER_CSS.values()
    )

    html_rows = ""
    for r in data:
        issue_html = "".join(f'<span class="issue">{html.escape(i)}</span>' for i in r['issues'])
        cover_html = '<span class="yes">✓</span>' if r['has_cover'] else '<span class="no">✗</span>'
        tc  = _tier_css(r['tier'])
        br  = f"{r['bitrate']} kbps"    if r.get('bitrate')      else '?'
        sr  = f"{r['sample_rate']} Hz"  if r.get('sample_rate')  else '?'
        bd  = f"{r['bit_depth']}-bit"   if r.get('bit_depth')    else '?'
        rc  = "has-issues" if r['issues'] else ""
        html_rows += (
            f'\n    <tr class="{rc}">'
            f'<td class="name" title="{html.escape(r["rel_path"])}">{html.escape(r["name"])}</td>'
            f'<td>{html.escape(r["format"])}</td><td>{br}</td><td>{sr}</td><td>{bd}</td>'
            f'<td>{r["duration_fmt"]}</td><td>{r["size_fmt"]}</td><td>{r["file_count"]}</td>'
            f'<td>{cover_html}</td>'
            f'<td><span class="tier {tc}">{html.escape(r["tier"])}</span></td>'
            f'<td>{issue_html}</td></tr>'
        )

    doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Audiobook Library — {html.escape(ROOT.name)}</title>
<style>
:root{{--bg:#0f1117;--surf:#1a1d2e;--bdr:#2a2d3e;--txt:#e0e0e0;--dim:#888;--acc:#7c6af0}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:2rem}}
h1{{font-size:1.6rem;margin-bottom:.25rem}}
.meta{{color:var(--dim);font-size:.82rem;margin-bottom:1.5rem}}
.stats{{display:flex;gap:1.2rem;margin-bottom:1.8rem;flex-wrap:wrap}}
.stat{{background:var(--surf);border:1px solid var(--bdr);border-radius:8px;padding:.75rem 1.1rem}}
.stat-val{{font-size:1.5rem;font-weight:700;color:var(--acc)}}
.stat-lbl{{font-size:.72rem;color:var(--dim);margin-top:.15rem;text-transform:uppercase;letter-spacing:.05em}}
.controls{{display:flex;gap:.8rem;margin-bottom:.9rem;flex-wrap:wrap;align-items:center}}
.controls input[type=text]{{background:var(--surf);border:1px solid var(--bdr);color:var(--txt);
  padding:.4rem .8rem;border-radius:6px;font-size:.83rem;width:220px}}
.controls label{{font-size:.82rem;color:var(--dim);display:flex;align-items:center;gap:.35rem;cursor:pointer}}
table{{width:100%;border-collapse:collapse;font-size:.8rem}}
th{{background:var(--surf);border-bottom:2px solid var(--bdr);padding:.55rem .7rem;text-align:left;
   color:var(--dim);font-weight:600;cursor:pointer;user-select:none;white-space:nowrap}}
th:hover{{color:var(--txt)}}
td{{padding:.42rem .7rem;border-bottom:1px solid var(--bdr);vertical-align:middle}}
tr:hover td{{background:#ffffff08}}
tr.has-issues td{{background:#7f1d1d14}}
td.name{{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}}
.yes{{color:#4ade80}}.no{{color:#f87171}}
.issue{{display:inline-block;background:#7f1d1d55;color:#fca5a5;border-radius:3px;
       padding:1px 5px;font-size:.72rem;margin:1px}}
.tier{{display:inline-block;border-radius:3px;padding:1px 7px;font-size:.73rem;font-weight:600}}
{tier_styles}
.hidden{{display:none!important}}
</style></head><body>
<h1>📚 Audiobook Library Report</h1>
<div class="meta">Root: {html.escape(str(ROOT))} · Generated: {now}</div>
<div class="stats">
  <div class="stat"><div class="stat-val">{len(data)}</div><div class="stat-lbl">Total Books</div></div>
  <div class="stat"><div class="stat-val">{fmt_size(total_size)}</div><div class="stat-lbl">Total Size</div></div>
  <div class="stat"><div class="stat-val">{issues_count}</div><div class="stat-lbl">With Issues</div></div>
  <div class="stat"><div class="stat-val">{sum(1 for r in data if r['has_cover'])}</div>
    <div class="stat-lbl">Have Cover</div></div>
  <div class="stat"><div class="stat-val">{sum(1 for r in data if not r['has_cover'])}</div>
    <div class="stat-lbl">Missing Cover</div></div>
</div>
<div class="controls">
  <input type="text" id="q" placeholder="Filter by name…" oninput="ft()">
  <label><input type="checkbox" id="iss" onchange="ft()"> Issues only</label>
  <label><input type="checkbox" id="noc" onchange="ft()"> Missing cover</label>
</div>
<table id="tbl"><thead><tr>
  <th onclick="st(0)">Title ↕</th><th onclick="st(1)">Format ↕</th>
  <th onclick="st(2)">Bitrate ↕</th><th onclick="st(3)">Sample Rate ↕</th>
  <th onclick="st(4)">Bit Depth ↕</th><th onclick="st(5)">Duration ↕</th>
  <th onclick="st(6)">Size ↕</th><th onclick="st(7)">Files ↕</th>
  <th onclick="st(8)">Cover ↕</th><th onclick="st(9)">Quality ↕</th><th>Issues</th>
</tr></thead><tbody>{html_rows}</tbody></table>
<script>
function ft(){{const q=document.getElementById('q').value.toLowerCase();
const io=document.getElementById('iss').checked;const nc=document.getElementById('noc').checked;
document.querySelectorAll('#tbl tbody tr').forEach(r=>{{
const nm=r.cells[0].textContent.toLowerCase();const hi=r.classList.contains('has-issues');
const hc=r.cells[8].textContent.trim()==='✓';
r.classList.toggle('hidden',!nm.includes(q)||(io&&!hi)||(nc&&hc));}});}}
let sd={{}};function st(c){{const tb=document.querySelector('#tbl tbody');
const rows=[...tb.querySelectorAll('tr')];sd[c]=!sd[c];
rows.sort((a,b)=>{{const x=a.cells[c].textContent.trim(),y=b.cells[c].textContent.trim();
const nx=parseFloat(x),ny=parseFloat(y);
if(!isNaN(nx)&&!isNaN(ny))return sd[c]?nx-ny:ny-nx;
return sd[c]?x.localeCompare(y):y.localeCompare(x);}});
rows.forEach(r=>tb.appendChild(r));}}
</script></body></html>"""

    out_path = ROOT / f"audiobook_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    out_path.write_text(doc, encoding='utf-8')
    good(f"Report saved → {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 5 — Corrupt / Truncated File Detection
# ─────────────────────────────────────────────────────────────────────────────

MIN_AUDIO_BYTES = 10_000
MIN_DURATION_S  = 1.0

def _check_audio_file(filepath):
    path = Path(filepath)
    try:
        size = path.stat().st_size
    except OSError:
        return {'path': filepath, 'issue': 'Cannot stat file', 'size': 0}

    if size == 0:
        return {'path': filepath, 'issue': 'Zero bytes', 'size': 0}
    if size < MIN_AUDIO_BYTES:
        return {'path': filepath, 'issue': f'Suspiciously small ({fmt_size(size)})', 'size': size}

    if HAS_FFMPEG:
        ai = ffprobe_info(filepath)
        if ai == 'TIMEOUT':
            return {'path': filepath,
                    'issue': f'ffprobe timed out after {FFPROBE_TIMEOUT}s — file may be unreadable, locked, or on a slow network share',
                    'size': size}
        if isinstance(ai, dict) and '_error' in ai:
            return {'path': filepath,
                    'issue': ai['_error'] or 'ffprobe could not read file',
                    'size': size}
        if ai is None:
            return {'path': filepath,
                    'issue': 'ffprobe could not read file — likely corrupt or unrecognised format',
                    'size': size}
        dur = ai.get('duration')
        if dur is not None and dur < MIN_DURATION_S:
            return {'path': filepath, 'issue': f'Audio duration only {dur:.2f}s — file may be truncated', 'size': size}
        return None

    if HAS_MUTAGEN:
        try:
            from mutagen import File as MF
            f = MF(filepath, easy=False)
            if f is None:
                return {'path': filepath, 'issue': 'Unrecognised format', 'size': size}
            fi = getattr(f, 'info', None)
            if fi:
                dur = getattr(fi, 'length', None)
                if dur is not None and dur < MIN_DURATION_S:
                    return {'path': filepath, 'issue': f'Duration {dur:.2f}s', 'size': size}
        except Exception as e:
            return {'path': filepath, 'issue': f'Parse error: {str(e)[:70]}', 'size': size}

    return None


def _repair_audio_file(filepath: Path, method: str) -> tuple:
    """
    Attempt to repair a corrupt/damaged audio file using ffmpeg.

    method:
      remux         — stream copy into fresh container (lossless, seconds)
      tolerant_copy — stream copy skipping bad frames (lossless for good frames)
      reencode      — full decode + re-encode (fixes stream corruption, lossy penalty)
    Returns (success: bool, message: str, output_path: Path | None)
    """
    if not HAS_FFMPEG:
        return False, 'ffmpeg not available', None

    suffix = filepath.suffix.lower()
    out_path = filepath.parent / f"{filepath.stem}_REPAIRED{suffix}"

    _codec_map = {
        '.mp3':  ['-c:a', 'libmp3lame', '-q:a', '2'],
        '.m4b':  ['-c:a', 'aac', '-b:a', '128k'],
        '.m4a':  ['-c:a', 'aac', '-b:a', '128k'],
        '.aac':  ['-c:a', 'aac', '-b:a', '128k'],
        '.flac': ['-c:a', 'flac'],
        '.ogg':  ['-c:a', 'libvorbis', '-q:a', '4'],
        '.opus': ['-c:a', 'libopus', '-b:a', '128k'],
        '.wav':  ['-c:a', 'pcm_s16le'],
    }
    encode_args = _codec_map.get(suffix, ['-c:a', 'copy'])

    if method == 'remux':
        # For m4a/m4b with broken/missing moov atom, force the container format
        # and use -movflags faststart to write a clean index.
        # -ignore_unknown lets ffmpeg skip unrecognised atoms rather than aborting.
        if suffix in ('.m4a', '.m4b', '.mp4', '.aac'):
            cmd = ['ffmpeg', '-y', '-hide_banner',
                   '-ignore_unknown',
                   '-i', str(filepath),
                   '-c', 'copy', '-map', '0:a?',
                   '-movflags', '+faststart',
                   str(out_path)]
        else:
            cmd = ['ffmpeg', '-y', '-hide_banner',
                   '-ignore_unknown',
                   '-i', str(filepath),
                   '-c', 'copy', '-map', '0:a?',
                   str(out_path)]
        desc = 'Re-muxing — rebuilding container index without re-encoding …'

    elif method == 'tolerant_copy':
        # -err_detect ignore_err skips corrupt frames rather than aborting.
        # For m4a/m4b also force the input format so ffmpeg doesn't give up
        # at the header before it even tries to read audio frames.
        force_fmt = ['-f', 'mp4'] if suffix in ('.m4a', '.m4b', '.mp4') else []
        cmd = (['ffmpeg', '-y', '-hide_banner',
                '-err_detect', 'ignore_err',
                '-ignore_unknown']
               + force_fmt
               + ['-i', str(filepath),
                  '-c', 'copy', '-map', '0:a?',
                  str(out_path)])
        desc = 'Tolerant copy — skipping corrupt/missing frames, keeping intact audio …'

    elif method == 'reencode':
        # Full decode + re-encode. Force input format for broken containers.
        force_fmt = ['-f', 'mp4'] if suffix in ('.m4a', '.m4b', '.mp4') else []
        cmd = (['ffmpeg', '-y', '-hide_banner',
                '-err_detect', 'ignore_err',
                '-ignore_unknown']
               + force_fmt
               + ['-i', str(filepath)]
               + encode_args
               + ['-map', '0:a?', str(out_path)])
        desc = f'Re-encoding — decoding everything readable, writing fresh {suffix.upper()} …'

    else:
        return False, f'Unknown repair method: {method}', None

    info(desc)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, err = proc.communicate(timeout=300)

        if proc.returncode != 0:
            fp_str    = str(filepath)
            err_lines = []
            for line in err.decode('utf-8', errors='replace').splitlines():
                s = line.strip()
                if s.startswith(fp_str):
                    s = s[len(fp_str):].lstrip(':').strip()
                if s:
                    err_lines.append(s)
            # Show last 2 meaningful lines — ffmpeg puts the key error near the end
            short_err = '  |  '.join(err_lines[-2:]) if err_lines else f'ffmpeg exit code {proc.returncode}'
            if out_path.exists():
                try: out_path.unlink()
                except Exception: pass
            return False, f'ffmpeg: {short_err}', None

        verify = ffprobe_info(out_path)
        if verify is None or verify == 'TIMEOUT' or (isinstance(verify, dict) and '_error' in verify):
            if out_path.exists():
                try: out_path.unlink()
                except Exception: pass
            return False, 'Repair produced an unreadable file — source may be too damaged', None

        out_dur  = (verify or {}).get('duration') or 0
        orig_ai  = ffprobe_info(filepath)
        orig_dur = (orig_ai or {}).get('duration') or 0 if isinstance(orig_ai, dict) else 0
        if orig_dur > 0:
            note = f'{out_dur:.0f}s recovered of {orig_dur:.0f}s ({out_dur/orig_dur*100:.0f}%)'
        else:
            note = f'{out_dur:.0f}s of audio recovered'
        return True, note, out_path

    except subprocess.TimeoutExpired:
        proc.kill(); proc.communicate()
        if out_path.exists():
            try: out_path.unlink()
            except Exception: pass
        return False, 'Repair timed out after 5 minutes', None
    except Exception as e:
        return False, f'Unexpected error: {e}', None


def _run_corrupt_repair(filepath: Path, rec: dict) -> bool:
    """
    Full interactive repair workflow for one corrupt file.
    Runs a deep diagnostic, shows exact ffprobe error, presents repair options,
    executes the chosen method, then handles original vs repaired.
    Returns True if dealt with (fixed/deleted), False if skipped.
    """
    print()
    if HAS_RICH:
        console.print(f"  [bold]File:[/bold]  {filepath.relative_to(ROOT)}")
        console.print(f"  [bold]Size:[/bold]  {fmt_size(rec['size'])}")
    else:
        print(f"  File : {filepath.relative_to(ROOT)}")
        print(f"  Size : {fmt_size(rec['size'])}")
    print()

    if HAS_RICH:
        console.print("  [dim]Running detailed diagnostic …[/dim]", end='\r')
    else:
        print("  Running detailed diagnostic …", end='\r', flush=True)

    diag = ffprobe_diagnose(filepath)
    print()

    def _dline(label, value, style="white"):
        if HAS_RICH:
            console.print(f"  [dim]{label:<22}[/dim] [{style}]{value}[/{style}]")
        else:
            print(f"  {label:<22} {value}")

    if diag['zero_bytes']:
        _dline("Status:", "Empty file (0 bytes) — unrecoverable", "red")
    elif diag['timeout']:
        _dline("Status:", "Timed out — may be a network/lock issue", "yellow")
    elif diag['truncated']:
        _dline("Status:", "Readable but audio is very short — likely truncated", "yellow")
    elif diag['salvageable'] and not diag['readable']:
        _dline("Status:", "Partially readable — some audio can be extracted", "yellow")
    elif diag['readable']:
        _dline("Status:", "Readable (flagged due to short duration or codec issue)", "green")
    else:
        _dline("Status:", "Unreadable — container or stream is damaged", "red")

    if diag['error']:
        _dline("ffprobe error:", diag['error'], "red")
    if diag['duration']:
        _dline("Duration found:", f"{diag['duration']:.1f}s  ({diag['duration']/60:.1f} min)")
    if diag['codec']:
        br = f"  @ {diag['bitrate']} kbps" if diag['bitrate'] else ""
        _dline("Codec:", f"{diag['codec']}{br}")
    print()

    # Build menu
    opts = []
    if 'remux' in diag['repair_options']:
        opts.append(('remux', 'r',
            '[R]e-mux',
            'Rebuild container without touching audio. Lossless & fast. ' +
            'Fixes bad headers/index. Best first attempt.'))
    if 'tolerant_copy' in diag['repair_options']:
        opts.append(('tolerant_copy', 't',
            '[T]olerant copy',
            'Copy audio while skipping corrupt frames. ' +
            'Some audio may be missing but what survives is lossless.'))
    if 'reencode' in diag['repair_options']:
        lossy = filepath.suffix.lower() in {'.mp3','.m4b','.m4a','.aac','.ogg','.opus'}
        codec = diag.get('codec') or filepath.suffix.lstrip('.').upper()
        opts.append(('reencode', 'e',
            '[E]ncode fresh',
            f'Decode everything ffmpeg can read, re-encode to {codec}. ' +
            f'Fixes stream corruption. ' +
            ('Note: quality loss (lossy→lossy).' if lossy else 'Lossless format — no quality loss.')))
    opts.append(('delete', 'd', '[D]elete', 'Permanently delete this file.'))
    opts.append(('skip',   's', '[S]kip',   'Leave unchanged and continue scanning.'))

    if HAS_RICH:
        console.print("  [bold]What would you like to do?[/bold]")
    else:
        print("  What would you like to do?")
    for _method, _key, label, desc in opts:
        if HAS_RICH:
            console.print(f"    [bold cyan]{label:<18}[/bold cyan] [dim]{desc}[/dim]")
        else:
            print(f"    {label:<18} {desc}")
    print()

    key_map = {o[1]: o[0] for o in opts}
    valid   = '/'.join(o[1] for o in opts)

    while True:
        raw = prompt_text(f"Choose [{valid}]").strip().lower()[:1]
        if raw in key_map:
            chosen = key_map[raw]
            break
        warn(f"Please enter one of: {valid}")

    if chosen == 'skip':
        info("Skipped — file left unchanged.")
        return False

    if chosen == 'delete':
        if not prompt_confirm(f"Permanently delete '{filepath.name}'?"):
            info("Cancelled.")
            return False
        try:
            filepath.unlink()
            good(f"Deleted: {filepath.name}")
            return True
        except Exception as e:
            bad(f"Delete failed: {e}")
            return False

    # Run repair
    success, message, out_path = _repair_audio_file(filepath, chosen)

    if not success:
        bad(f"Repair failed: {message}")
        print()
        if prompt_confirm("Try a different repair method?"):
            return _run_corrupt_repair(filepath, rec)
        return False

    good(f"Repair succeeded: {message}")
    if HAS_RICH:
        console.print(f"  [dim]Repaired file: {out_path.name}[/dim]")
    else:
        print(f"  Repaired file: {out_path.name}")
    print()

    # Keep both or replace?
    if HAS_RICH:
        console.print("  [bold]Original vs repaired:[/bold]")
        console.print("    [bold cyan][K]eep both[/bold cyan]  [dim]Repaired file sits alongside original[/dim]")
        console.print("    [bold cyan][R]eplace[/bold cyan]    [dim]Delete original, rename repaired to original filename[/dim]")
    else:
        print("  [K]eep both   Repaired file sits alongside original")
        print("  [R]eplace     Delete original, rename repaired to original filename")
    print()

    while True:
        raw = prompt_text("Choose [k/r]").strip().lower()[:1]
        if raw == 'k':
            info(f"Both files kept.")
            return True
        if raw == 'r':
            break
        warn("Please enter k or r")

    try:
        filepath.unlink()
        out_path.rename(filepath)
        good(f"Original replaced: {filepath.name}")
        return True
    except Exception as e:
        bad(f"Could not rename repaired file: {e}")
        bad(f"Repaired file is at: {out_path}")
        return True

def detect_corrupt_files():
    banner("Corrupt / Truncated File Detection")
    if not HAS_MUTAGEN and not HAS_FFMPEG:
        warn("Requires mutagen or FFmpeg. Please install one and restart.")
        return

    book_dirs = find_book_dirs()
    if not book_dirs:
        warn("No audiobook directories found.")
        return

    all_audio = []
    for d in book_dirs:
        all_audio.extend(get_audio_files(d))

    info(f"Checking {len(all_audio)} audio file(s) across {NUM_WORKERS} worker(s) …")
    flagged = []

    if HAS_RICH:
        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                      BarColumn(), MofNCompleteColumn(), console=console) as prog:
            task = prog.add_task("Scanning …", total=len(all_audio))
            with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
                for f, result in zip(all_audio, pool.map(_check_audio_file, all_audio)):
                    prog.update(task, advance=1, description=f.name[:45])
                    if result:
                        flagged.append(result)
    else:
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
            for i, (f, result) in enumerate(zip(all_audio,
                                                pool.map(_check_audio_file, all_audio))):
                if i % 50 == 0:
                    print(f"  {i}/{len(all_audio)} …", end='\r')
                if result:
                    flagged.append(result)
        print()

    if not flagged:
        good(f"All {len(all_audio)} files appear healthy.")
        return

    warn(f"Found {len(flagged)} suspect file(s):")
    print_table(
        ["#","File","Issue","Size"],
        [(str(i+1), str(Path(r['path']).relative_to(ROOT)), r['issue'], fmt_size(r['size']))
         for i, r in enumerate(flagged)],
        title="Suspect Files"
    )
    print()
    info("Enter numbers to delete (e.g. '1 3'), 'a' = all, 's' = skip")
    choice = prompt_text("Selection", default="s")

    if choice.lower() in ('s','skip',''): return

    to_del = (list(range(1, len(flagged)+1)) if choice.lower() in ('a','all')
              else [int(x) for x in choice.split() if x.isdigit()])

    deleted = 0
    for idx in to_del:
        if 1 <= idx <= len(flagged):
            fp = Path(flagged[idx-1]['path'])
            if prompt_confirm(f"Confirm delete '{fp.name}' ({flagged[idx-1]['issue']})?"):
                try:
                    fp.unlink()
                    deleted += 1
                    good(f"Deleted: {fp.name}")
                except Exception as e:
                    bad(f"Failed: {e}")
    good(f"Deleted {deleted} file(s).")


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 6 — Multi-Disc Detection & Consolidation  (embedded renamer)
# ─────────────────────────────────────────────────────────────────────────────

def _common_prefix_ratio(names):
    if not names: return 0.0
    prefix  = os.path.commonprefix(names)
    avg_len = sum(len(n) for n in names) / len(names)
    return len(prefix) / avg_len if avg_len else 0.0

def _detect_multidisc_dirs():
    """
    Walk ROOT looking for directories whose immediate sub-dirs all contain audio
    and appear to be disc/part/cd splits of the same audiobook.
    Returns list of (parent_dir, [disc_subdirs]).
    """
    results = []
    visited = set()

    for dirpath, dirnames, _ in os.walk(ROOT):
        p = Path(dirpath)
        if p in visited: continue

        subdirs    = sorted([p / d for d in dirnames], key=natural_sort_key)
        disc_subs  = [s for s in subdirs if get_audio_files(s)]
        if len(disc_subs) < 2: continue

        sub_names = [s.name for s in disc_subs]

        # ── Rule 1: explicit disc/cd/part markers present ─────────────────────
        # This is the primary signal.  At least ONE sub-dir must have an
        # unambiguous disc marker.  Similarity alone is never enough — a
        # series of related books (Harry Potter 1/2/3) would also score high.
        any_disc = any(DISC_PATTERNS.search(n) for n in sub_names)

        if not any_disc:
            continue   # no disc markers → not a disc split, regardless of similarity

        # ── Rule 2: the sub-dirs are plausibly the SAME work, not a series ────
        # When disc markers ARE present (e.g. "Dune Part 1", "Dune Part 2")
        # verify the non-numeric portions are actually similar, so we don't
        # consolidate a folder that just happens to contain unrelated sub-dirs
        # each labelled "Part N".
        prefix_r = _common_prefix_ratio(sub_names)
        pairs = []
        for i in range(len(disc_subs)):
            for j in range(i+1, len(disc_subs)):
                ni, nj = normalize_name(sub_names[i]), normalize_name(sub_names[j])
                if ni and nj: pairs.append(fuzzy_score(ni, nj))
        avg_sim = sum(pairs) / len(pairs) if pairs else 0

        # With disc markers confirmed, require only moderate similarity (≥50%)
        # OR a shared prefix of at least 40% — much looser than without markers.
        if prefix_r >= 0.40 or avg_sim >= 50:
            results.append((p, disc_subs))
            visited.update(disc_subs)
            dirnames.clear()

    return results


# ── Disc review interactive loop ─────────────────────────────────────────────

_DISC_HELP = """
  ┌──────────────────────────────────────────────────────────┐
  │  Commands:                                               │
  │    Enter          Accept order & continue to preview     │
  │    show <N>       List all tracks in disc N              │
  │    swap <A> <B>   Swap two discs                         │
  │    move <A> <B>   Move disc A to position B              │
  │    remove <N>     Drop disc N from consolidation list    │
  │    rename <N>     Change the display label for disc N    │
  │    q              Cancel — no files will be changed      │
  └──────────────────────────────────────────────────────────┘"""

def _disc_idx(token, length):
    try:
        n = int(token)
        if not (1 <= n <= length): raise ValueError
        return n - 1
    except ValueError:
        warn(f"'{token}' is not a valid disc number (1–{length}).")
        return None

def _show_disc_table(discs, labels):
    print_table(
        ["#","Tracks","Folder / Label"],
        [(str(i+1), str(len(get_audio_files(d))), lbl)
         for i,(d,lbl) in enumerate(zip(discs,labels))]
    )

def _disc_review(discs, parent):
    """Interactive disc-order review. Returns (discs, labels) or raises SystemExit."""
    labels = [d.name for d in discs]
    info(f"Detected {len(discs)} disc folder(s):")
    _show_disc_table(discs, labels)
    print(_DISC_HELP)

    while True:
        raw   = input("  disc> ").strip()
        parts = raw.split()
        cmd   = parts[0].lower() if parts else ''

        if raw == '':
            if not discs:
                bad("No discs left — aborting.")
                raise SystemExit
            return discs, labels

        if cmd in ('q','quit','cancel','exit'):
            info("Consolidation cancelled — no files changed.")
            raise SystemExit

        if cmd == 'show' and len(parts) == 2:
            n = _disc_idx(parts[1], len(discs))
            if n is None: continue
            files = get_audio_files(discs[n])
            print(f"\n  Disc {n+1} — {labels[n]}  ({len(files)} tracks)")
            for j, f in enumerate(files, 1): print(f"    {j:>4}.  {f.name}")
            print()

        elif cmd == 'swap' and len(parts) == 3:
            a, b = _disc_idx(parts[1], len(discs)), _disc_idx(parts[2], len(discs))
            if a is None or b is None: continue
            discs[a], discs[b] = discs[b], discs[a]
            labels[a], labels[b] = labels[b], labels[a]
            good(f"Swapped disc {a+1} ↔ {b+1}.")
            _show_disc_table(discs, labels)

        elif cmd == 'move' and len(parts) == 3:
            a, b = _disc_idx(parts[1], len(discs)), _disc_idx(parts[2], len(discs))
            if a is None or b is None: continue
            discs.insert(b, discs.pop(a))
            labels.insert(b, labels.pop(a))
            good(f"Moved disc to position {b+1}.")
            _show_disc_table(discs, labels)

        elif cmd == 'remove' and len(parts) == 2:
            n = _disc_idx(parts[1], len(discs))
            if n is None: continue
            removed = labels[n]
            discs.pop(n); labels.pop(n)
            good(f"Removed '{removed}'.")
            _show_disc_table(discs, labels)

        elif cmd == 'rename' and len(parts) == 2:
            n = _disc_idx(parts[1], len(discs))
            if n is None: continue
            new_lbl = input(f"  New label for disc {n+1} (was '{labels[n]}'): ").strip()
            if new_lbl:
                labels[n] = new_lbl
                good("Label updated.")
                _show_disc_table(discs, labels)
        else:
            warn(f"Unknown command: '{raw}'")
            print(_DISC_HELP)


def _file_preview(all_files, author, title, parent, discs, labels):
    """
    Show sample renames. Returns True (proceed), None (go back), or raises SystemExit.
    """
    total = len(all_files)
    print()
    h2(f"Output pattern:  {author} - {title} - ###<ext>")
    info(f"Total files to rename: {total}")
    print()

    counter = 1
    for disc, lbl in zip(discs, labels):
        files = get_audio_files(disc)
        if not files: continue
        start, end = counter, counter + len(files) - 1
        info(f"  {lbl:<45}  tracks {start:03d}–{end:03d}  ({len(files)} files)")
        counter += len(files)
    if get_audio_files(parent):
        info(f"  Root-level files: {len(get_audio_files(parent))} (numbered first)")

    print()
    # Sample rows: first 3 and last 3
    if total <= 6:
        samples = list(enumerate(all_files))
    else:
        samples = (list(enumerate(all_files[:3]))
                   + [None]
                   + list(enumerate(all_files[-3:], total-3)))

    rows = []
    for item in samples:
        if item is None:
            rows.append(("…", "…", "…"))
        else:
            i, f = item
            rows.append((str(i+1), f.name[:40], f"{author} - {title} - {i+1:03d}{f.suffix}"))
    print_table(["#","Source File","New Name"], rows, title="File Preview (sample)")

    print()
    print("  Options:  Enter = proceed   b = back to disc review   q = cancel")
    while True:
        c = input("  preview> ").strip().lower()
        if c == '':    return True
        if c in ('b','back'): return None
        if c in ('q','quit','cancel','exit'):
            info("Consolidation cancelled — no files changed.")
            raise SystemExit
        warn("Enter, 'b' to go back, or 'q' to cancel.")


def _execute_consolidation(all_files, author, title, parent, disc_folders):
    moved, errors = [], []
    print()
    for i, src in enumerate(all_files, 1):
        new_name = f"{author} - {title} - {i:03d}{src.suffix}"
        dest     = parent / new_name
        if src.parent == parent and src.name == new_name:
            moved.append(dest); continue
        if dest.exists() and dest != src:
            msg = f"Skipped (target exists): {new_name}"
            warn(msg); errors.append(msg); continue
        try:
            shutil.move(str(src), str(dest))
            dim(f"    {src.name}  →  {new_name}")
            moved.append(dest)
        except Exception as e:
            msg = f"{src.name}: {e}"
            bad(f"ERROR {msg}"); errors.append(msg)

    removed = []
    for d in disc_folders:
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed.append(d.name)
                good(f"Removed empty folder: {d.name}")
            else:
                warn(f"Folder not empty, left in place: {d.name}  "
                     f"{[f.name for f in d.iterdir()]}")
        except Exception as e:
            errors.append(f"Could not remove {d.name}: {e}")

    print()
    good(f"{len(moved)} file(s) renamed,  {len(removed)} folder(s) removed.")
    if errors:
        warn(f"{len(errors)} warning(s):")
        for e in errors: dim(f"    - {e}")


def consolidate_multidisc_book(parent, disc_subs):
    """
    Full interactive flow for one multi-disc book.
    Returns True if completed, False if skipped/cancelled.
    """
    banner(f"Consolidate: {parent.name}")

    total_files = sum(len(get_audio_files(d)) for d in disc_subs)
    root_audio  = get_audio_files(parent)
    info(f"Parent directory : {parent.relative_to(ROOT)}")
    info(f"Disc sub-folders : {len(disc_subs)}")
    info(f"Total audio files: {total_files + len(root_audio)}")
    print()
    for i, d in enumerate(disc_subs, 1):
        info(f"  [{i}] {d.name}  ({len(get_audio_files(d))} files)")
    print()

    if not prompt_confirm("Proceed with consolidation?", default=True):
        info("Skipped.")
        return False

    inferred_author, inferred_title, _ = _extract_parts(parent.name)
    print()
    author = prompt_text("Author name", default=inferred_author or "")
    while not author.strip():
        warn("Author cannot be empty.")
        author = prompt_text("Author name")
    title  = prompt_text("Book title",  default=inferred_title or parent.name)
    while not title.strip():
        warn("Title cannot be empty.")
        title = prompt_text("Book title")
    author = sanitize_filename(author)
    title  = sanitize_filename(title)

    try:
        discs, labels = _disc_review(list(disc_subs), parent)
        while True:
            all_files = root_audio + [f for d in discs for f in get_audio_files(d)]
            result    = _file_preview(all_files, author, title, parent, discs, labels)
            if result is None:
                discs, labels = _disc_review(discs, parent)
                continue
            if result: break

        warn(f"This will permanently rename {len(all_files)} files in '{parent.name}'.")
        if not prompt_confirm("Final confirmation — proceed?"):
            info("Cancelled — no files changed.")
            return False

        _execute_consolidation(all_files, author, title, parent, discs)
        return True

    except SystemExit:
        return False


def scan_multidisc():
    banner("Multi-Disc Detection & Consolidation")
    info("Scanning for books split into disc/part sub-folders …")
    found = _detect_multidisc_dirs()

    if not found:
        good("No multi-disc books detected.")
        return

    info(f"Found {len(found)} potential multi-disc book(s):\n")
    for i, (parent, discs) in enumerate(found, 1):
        total = sum(len(get_audio_files(d)) for d in discs)
        print(f"  [{i}] {parent.name}  ({len(discs)} discs, {total} files)")
        for d in discs: dim(f"        └─ {d.name}  ({len(get_audio_files(d))} files)")
    print()

    info("Enter numbers to consolidate (e.g. '1 3'), 'a' = all, Enter = skip all")
    choice = prompt_text("Selection", default="")

    if not choice.strip() or choice.lower() in ('s', 'skip'):
        info("No books selected.")
        return

    to_process = (
        list(range(len(found))) if choice.lower() in ('a','all')
        else [int(x)-1 for x in choice.split()
              if x.isdigit() and 1 <= int(x) <= len(found)]
    )
    for idx in to_process:
        parent, disc_subs = found[idx]
        consolidate_multidisc_book(parent, disc_subs)
        print()


# ─────────────────────────────────────────────────────────────────────────────
#  Feature 7 — Sequential Issue Scanner  (live walk-and-fix)
#
#  Design:
#    • Discovers all book dirs first, shows total count.
#    • Phase 1: Walks every directory one-by-one, printing each one as it's
#      checked.  Per-dir issues (junk · multi-disc · corrupt files) surface
#      immediately — the scan pauses, you fix (or skip), then continues.
#    • Phase 2: Duplicate detection across the full remaining library, again
#      showing each comparison and pausing on every group found.
#    • 'q' at any prompt exits the scanner with a summary.
#    • All decisions are final (no undo).
# ─────────────────────────────────────────────────────────────────────────────

_DUP_THRESHOLD = 75


def _dir_status_line(idx: int, total: int, rel_name: str, audio_count: int,
                     has_cover: bool, fmt_hint: str = ""):
    """Print a single informational line while the scan is running."""
    pad      = len(str(total))
    cover_mk = "✓" if has_cover else "✗ no cover"
    extras   = f"{audio_count} file(s)  cover:{cover_mk}"
    if fmt_hint:
        extras += f"  [{fmt_hint}]"
    # Truncate long directory names so lines stay on one screen line
    max_name = max(30, 62 - 2*pad - len(extras))
    display  = rel_name if len(rel_name) <= max_name else "…" + rel_name[-(max_name-1):]

    if HAS_RICH:
        cover_style = "green" if has_cover else "yellow"
        console.print(
            f"  [dim][{idx:>{pad}}/{total}][/dim]  "
            f"[white]{display}[/white]"
            f"  [dim]{audio_count} file(s)[/dim]"
            f"  cover:[{cover_style}]{'✓' if has_cover else '✗'}[/{cover_style}]"
            + (f"  [dim]{fmt_hint}[/dim]" if fmt_hint else ""),
            highlight=False
        )
    else:
        print(f"  [{idx:>{pad}}/{total}]  {display:<{max_name}}  {extras}")


def _dup_status_line(idx: int, total: int, dir_name: str, remaining: int):
    pad     = len(str(total))
    display = dir_name if len(dir_name) <= 48 else "…" + dir_name[-47:]
    if HAS_RICH:
        console.print(
            f"  [dim][{idx:>{pad}}/{total}][/dim]  "
            f"[white]{display}[/white]  "
            f"[dim]{remaining} to compare[/dim]",
            highlight=False
        )
    else:
        print(f"  [{idx:>{pad}}/{total}]  {display}  ({remaining} to compare)")


def _issue_prompt(iss: 'Issue', issue_num: int, resolved: int, skipped: int) -> str:
    """
    Display a found-issue panel and return 'fix', 'skip', or 'quit'.
    issue_num is the running count of issues found so far (not a total).
    """
    sev_color = {"error": "red", "warning": "yellow", "info": "cyan"}
    color     = sev_color.get(iss.severity, "cyan")

    if HAS_RICH:
        console.print()
        console.print(Panel(
            f"[bold white]{iss.category}[/bold white]\n"
            f"[dim]{iss.detail}[/dim]",
            title=f"[bold {color}] Issue #{issue_num} — {iss.severity.upper()} [/bold {color}]"
                  f"  [dim](resolved {resolved} · skipped {skipped})[/dim]",
            border_style=color,
            padding=(0, 2),
        ))
    else:
        print(f"\n{'═'*62}")
        print(f"  Issue #{issue_num}  [{iss.severity.upper()}]  —  {iss.category}")
        print(f"  Resolved: {resolved}   Skipped: {skipped}")
        print(f"{'─'*62}")
        print(f"  {iss.detail}")
        print(f"{'═'*62}")

    print()
    if iss.path:
        try:    rel = iss.path.relative_to(ROOT)
        except ValueError: rel = iss.path
        info(f"Location: {rel}")
        print()

    if HAS_RICH:
        console.print(
            "  [bold green]\\[f][/bold green][green]ix[/green]   "
            "[bold yellow]\\[s][/bold yellow][yellow]kip[/yellow]   "
            "[bold red]\\[q][/bold red][red]uit scanner[/red]"
        )
    else:
        print("  [f]ix   [s]kip   [q]uit scanner")
    print()

    while True:
        raw = input("  > ").strip().lower()
        if raw in ('q', 'quit', 'exit'):    return 'quit'
        if raw in ('s', 'skip', ''):        return 'skip'
        if raw in ('f', 'fix', 'y', 'yes'): return 'fix'
        warn("Enter  f = fix   s = skip   q = quit")


def _run_issue(iss: 'Issue', issue_num: int, resolved: int, skipped: int):
    """
    Present one issue, run resolver if chosen.
    Returns (resolved_delta, skipped_delta, quit_flag).
    """
    action = _issue_prompt(iss, issue_num, resolved, skipped)

    if action == 'quit':
        return 0, 0, True

    if action == 'skip':
        info("Skipped.")
        return 0, 1, False

    # fix
    try:
        result = iss.resolve_fn(iss)
        return (1, 0, False) if result else (0, 1, False)
    except SystemExit:
        info("Action cancelled.")
        return 0, 1, False
    except KeyboardInterrupt:
        print(); warn("Interrupted.")
        return 0, 1, False
    except Exception as e:
        bad(f"Unexpected error during fix: {e}")
        return 0, 1, False


# ── Multi-disc helper used by the live scanner ───────────────────────────────

def _check_dir_multidisc(book_dir: Path, already_seen: set):
    """
    Check whether book_dir's PARENT looks like a multi-disc container
    that hasn't been reported yet.  Returns (parent, disc_subs) or None.
    """
    parent = book_dir.parent
    if parent in already_seen or parent == ROOT:
        return None

    sibling_audio_dirs = sorted(
        [d for d in parent.iterdir() if d.is_dir() and get_audio_files(d)],
        key=natural_sort_key
    )
    if len(sibling_audio_dirs) < 2:
        return None

    sub_names = [d.name for d in sibling_audio_dirs]

    # Disc markers are required — similarity alone is never enough
    any_disc  = any(DISC_PATTERNS.search(n) for n in sub_names)
    if not any_disc:
        return None

    prefix_r  = _common_prefix_ratio(sub_names)
    pairs = []
    for ii in range(len(sibling_audio_dirs)):
        for jj in range(ii+1, len(sibling_audio_dirs)):
            ni, nj = normalize_name(sub_names[ii]), normalize_name(sub_names[jj])
            if ni and nj: pairs.append(fuzzy_score(ni, nj))
    avg_sim = sum(pairs)/len(pairs) if pairs else 0

    if prefix_r >= 0.40 or avg_sim >= 50:
        return parent, sibling_audio_dirs
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Scan cache  —  skip already-clean directories across sessions
#
#  Cache file:  ROOT/.audiobook_cache.json
#  Log file:    ROOT/.audiobook_scan.log
#
#  Cache key  : directory path relative to ROOT
#  Cache value: {
#      "fingerprint": "<sha1 of name+size+mtime for every audio file>",
#      "scanned_at" : "2025-03-07T18:49:15",
#      "file_count" : 12,
#      "fmt"        : "MP3"
#  }
#
#  A cached entry is valid only when the directory fingerprint matches.
#  Directories with known issues are NEVER cached — they always re-scan.
# ─────────────────────────────────────────────────────────────────────────────

class ScanCache:
    """
    Thread-safe read/write cache.  Workers read (is_clean_cached) and write
    (mark_clean / mark_dirty) concurrently; a lock protects the dict.
    Saved to disk at the end of each successful scan session.
    """

    def __init__(self):
        self._lock  = threading.Lock()
        self._data: dict = {}   # key → {fingerprint, scanned_at, file_count, fmt}
        self._hits  = 0
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self):
        try:
            if CACHE_FILE.exists():
                raw = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
                # Validate top-level structure
                if isinstance(raw, dict):
                    self._data = raw
        except Exception:
            self._data = {}

    def save(self):
        """Write the current cache to disk.  Call after scan completes."""
        try:
            tmp = CACHE_FILE.with_suffix('.tmp')
            tmp.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
            tmp.replace(CACHE_FILE)
        except Exception as e:
            pass   # non-fatal — cache is advisory only

    # ── Fingerprinting ───────────────────────────────────────────────────────

    @staticmethod
    def fingerprint(book_dir: Path) -> str:
        """
        Stable, fast fingerprint of a directory's audio file set.
        Uses only stat() — no file reads.  Changes if any file is
        added, removed, renamed, resized, or touched.
        """
        parts = []
        try:
            for f in sorted(book_dir.iterdir()):
                if f.suffix.lower() in AUDIO_EXTS:
                    st = f.stat()
                    parts.append(f"{f.name}:{st.st_size}:{st.st_mtime_ns}")
        except Exception:
            return ""
        raw = "\n".join(parts).encode()
        return hashlib.sha1(raw).hexdigest()

    # ── Public API ───────────────────────────────────────────────────────────

    def is_clean_cached(self, book_dir: Path) -> bool:
        """
        Return True if this directory was previously scanned clean AND its
        fingerprint still matches.  Thread-safe.
        """
        key = str(book_dir.relative_to(ROOT))
        with self._lock:
            entry = self._data.get(key)
        if not entry:
            return False
        current_fp = self.fingerprint(book_dir)
        if current_fp and current_fp == entry.get('fingerprint'):
            with self._lock:
                self._hits += 1
            return True
        # Fingerprint changed — evict stale entry
        with self._lock:
            self._data.pop(key, None)
        return False

    def mark_clean(self, book_dir: Path, file_count: int, fmt: str):
        """Record that a directory scanned completely clean."""
        key = str(book_dir.relative_to(ROOT))
        fp  = self.fingerprint(book_dir)
        if not fp:
            return
        with self._lock:
            self._data[key] = {
                'fingerprint': fp,
                'scanned_at':  datetime.now().isoformat(timespec='seconds'),
                'file_count':  file_count,
                'fmt':         fmt,
            }

    def mark_dirty(self, book_dir: Path):
        """Remove a directory from the cache (issues found or files changed)."""
        key = str(book_dir.relative_to(ROOT))
        with self._lock:
            self._data.pop(key, None)

    def evict_missing(self, current_dirs: list):
        """Remove cache entries for directories that no longer exist."""
        current_keys = {str(d.relative_to(ROOT)) for d in current_dirs}
        with self._lock:
            stale = [k for k in self._data if k not in current_keys]
            for k in stale:
                del self._data[k]
        return len(stale)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)


class ScanLogger:
    """
    Appends human-readable lines to ROOT/.audiobook_scan.log.
    Each scan session gets a header block.  Thread-safe via a lock.
    """

    def __init__(self):
        self._lock    = threading.Lock()
        self._session = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._lines: List[str] = []   # buffered until flush()

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def session_start(self, total_dirs: int, cache_size: int):
        sep = "─" * 72
        self._lines += [
            "",
            sep,
            f"  SCAN SESSION  {self._session}",
            f"  Library root : {ROOT}",
            f"  Directories  : {total_dirs}",
            f"  Cache entries: {cache_size}",
            sep,
        ]

    def skipped(self, book_dir: Path, entry: dict):
        rel = str(book_dir.relative_to(ROOT))
        self._lines.append(
            f"  [{self._ts()}] SKIP  {rel}"
            f"  (cached clean {entry.get('scanned_at','?')[:10]},"
            f" {entry.get('file_count','?')} files)"
        )

    def clean(self, book_dir: Path, file_count: int, fmt: str):
        rel = str(book_dir.relative_to(ROOT))
        self._lines.append(
            f"  [{self._ts()}] CLEAN {rel}  ({file_count} files, {fmt})"
        )

    def issue(self, book_dir: Path, category: str, summary: str):
        rel = str(book_dir.relative_to(ROOT))
        self._lines.append(
            f"  [{self._ts()}] ISSUE [{category}]  {rel}  —  {summary}"
        )

    def resolved(self, category: str, summary: str):
        self._lines.append(
            f"  [{self._ts()}] FIXED [{category}]  {summary}"
        )

    def skipped_issue(self, category: str, summary: str):
        self._lines.append(
            f"  [{self._ts()}] SKIP  [{category}]  {summary}"
        )

    def session_end(self, scanned: int, skipped_cached: int,
                    issues_found: int, resolved: int):
        self._lines += [
            f"  [{self._ts()}] DONE  "
            f"scanned={scanned}  cached_skip={skipped_cached}  "
            f"issues={issues_found}  resolved={resolved}",
        ]

    def flush(self):
        """Append buffered lines to the log file."""
        if not self._lines:
            return
        try:
            with LOG_FILE.open('a', encoding='utf-8') as fh:
                fh.write("\n".join(self._lines) + "\n")
        except Exception:
            pass   # non-fatal
        self._lines.clear()


# Singletons created once at startup, used throughout the session
scan_cache  = ScanCache()
scan_logger = ScanLogger()


# ─────────────────────────────────────────────────────────────────────────────
#  Scan state  —  thread-safe shared state between main and worker threads
# ─────────────────────────────────────────────────────────────────────────────

class ScanState:
    """
    All fields written by worker threads, read by the main/display thread.
    Uses a single lock for simplicity — updates are tiny and infrequent.
    """
    def __init__(self):
        self._lock            = threading.Lock()
        self.current_dir      = ""
        self.current_file     = ""
        self.file_started_at  = 0.0   # monotonic time when current file probe began
        self.active_files: dict = {}  # {filename: start_time} — all in-flight probes
        self.log: deque       = deque(maxlen=14)
        self.paused           = threading.Event()
        self.paused.set()
        self.abort            = threading.Event()
        self.done             = 0
        self.total            = 0

    def set_current(self, dir_name: str, file_name: str = ""):
        import time
        with self._lock:
            self.current_dir  = dir_name
            self.current_file = file_name
            self.file_started_at = time.monotonic() if file_name else 0.0

    def file_started(self, file_name: str):
        """Register that a probe has begun on file_name."""
        import time
        with self._lock:
            self.active_files[file_name] = time.monotonic()

    def file_finished(self, file_name: str):
        """Deregister a completed or timed-out probe."""
        with self._lock:
            self.active_files.pop(file_name, None)

    def log_entry(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.log.append((ts, level, msg))

    def advance(self):
        with self._lock:
            self.done += 1

    def wait_if_paused(self) -> bool:
        """Block while paused. Returns False if abort was requested."""
        self.paused.wait()
        return not self.abort.is_set()

    def is_aborted(self) -> bool:
        return self.abort.is_set()

    def snapshot(self):
        """Return a consistent read-only copy for the display thread."""
        import time
        with self._lock:
            now = time.monotonic()
            active = {
                fn: now - started
                for fn, started in self.active_files.items()
            }
            return {
                'dir':          self.current_dir,
                'file':         self.current_file,
                'file_elapsed': (now - self.file_started_at) if self.file_started_at else 0.0,
                'active_files': active,   # {filename: elapsed_seconds}
                'log':          list(self.log),
                'done':         self.done,
                'total':        self.total,
                'paused':       not self.paused.is_set(),
            }


# ─────────────────────────────────────────────────────────────────────────────
#  Keyboard listener  —  daemon thread, P = pause/resume, Q = abort
# ─────────────────────────────────────────────────────────────────────────────

def _start_keyboard_thread(state: ScanState):
    """
    Background daemon thread — reads P (pause/resume) and Q (abort).
    Windows:   uses msvcrt.kbhit() / msvcrt.getwch()  (no special setup needed)
    Unix/macOS: uses tty.setcbreak + select
    Falls back silently if neither is available (headless / piped stdin).
    """
    def _toggle_pause():
        if state.paused.is_set():   # currently running → pause
            state.paused.clear()
            state.log_entry("pause", "⏸  PAUSED — press P to resume")
        else:                        # currently paused → resume
            state.paused.set()
            state.log_entry("good", "▶  RESUMED")

    def _do_abort():
        state.abort.set()
        state.paused.set()          # unblock any waiting workers
        state.log_entry("error", "✗  Scan aborted by user (Q)")

    def _windows_reader():
        import msvcrt
        while not state.is_aborted():
            if msvcrt.kbhit():
                ch = msvcrt.getwch().lower()
                if ch == 'p':
                    _toggle_pause()
                elif ch in ('q', '\x03'):
                    _do_abort()
            threading.Event().wait(0.05)   # 50 ms poll

    def _unix_reader():
        import tty, termios
        if not sys.stdin.isatty():
            return
        fd  = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd, termios.TCSANOW)
            while not state.is_aborted():
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                if ready:
                    ch = sys.stdin.read(1).lower()
                    if ch == 'p':
                        _toggle_pause()
                    elif ch in ('q', '\x03'):
                        _do_abort()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _reader():
        if platform.system() == 'Windows':
            try:
                _windows_reader()
            except Exception:
                pass
        else:
            try:
                _unix_reader()
            except Exception:
                pass

    t = threading.Thread(target=_reader, daemon=True, name="kb-listener")
    t.start()
    return t


# ─────────────────────────────────────────────────────────────────────────────
#  Live display  —  two-panel Rich renderable built from ScanState
# ─────────────────────────────────────────────────────────────────────────────

def _make_live_display(state: ScanState, phase: str) -> 'RGroup':
    import time as _time
    snap  = state.snapshot()
    done  = snap['done']
    total = max(snap['total'], 1)
    pct   = done / total

    # Spinner frame driven by wall clock — animates at ~12 fps via Live refresh
    spin_frame = _SPIN[int(_time.monotonic() * 10) % len(_SPIN)]

    # ── Progress bar ──────────────────────────────────────────────────────────
    bar_w  = 40
    filled = int(bar_w * pct)
    bar    = f"[cyan]{'█' * filled}[/cyan][dim]{'░' * (bar_w - filled)}[/dim]"

    # ── Status line with animated spinner ────────────────────────────────────
    active = snap['active_files']   # {filename: elapsed_seconds}
    if snap['paused']:
        status = "[bold yellow]⏸  PAUSED[/bold yellow]"
    elif active:
        status = f"[bold green]{spin_frame}  Analyzing {len(active)} audio file(s) with ffprobe …[/bold green]"
    else:
        status = f"[bold cyan]{spin_frame}  Reading folder …[/bold cyan]"
    keys = "[dim]   \\[P] pause/resume   \\[Q] abort[/dim]"

    def trunc(s, n=62):
        return s if len(s) <= n else "…" + s[-(n - 1):]

    cur_dir = snap['dir']

    # ── Progress counter — "folders" not bare numbers ─────────────────────────
    progress_line = (
        f"  {bar}  "
        f"[cyan]{done}[/cyan][dim] of [/dim][cyan]{total}[/cyan] "
        f"[dim]audiobook folders checked  ({pct * 100:.0f}%)[/dim]"
    )

    # ── Active ffprobe lines — one per in-flight file with elapsed timer ──────
    BAR_W = 20
    file_lines = []
    for fname, elapsed in sorted(active.items(), key=lambda kv: -kv[1]):
        e = int(elapsed)

        if elapsed >= FFPROBE_TIMEOUT:
            name_style = "red"
            timer      = f"[bold red]{e}s  ⚠ stalled[/bold red]"
        elif elapsed >= 3:
            name_style = "yellow"
            timer      = f"[yellow]{e}s[/yellow]"
        else:
            name_style = "white"
            timer      = f"[dim]{e}s[/dim]"

        # Single bouncing pip so the line looks alive
        pip = _SPIN[int(_time.monotonic() * 10) % len(_SPIN)]

        file_lines.append(
            f"  [dim cyan]{pip}[/dim cyan]  [{name_style}]{trunc(fname, 52)}[/{name_style}]"
            f"  {timer}"
        )

    if not file_lines and cur_dir:
        dots = "·" * (int(_time.monotonic() * 3) % 4)
        file_lines = [f"  [dim cyan]  {spin_frame}  reading audio files{dots:<3}[/dim cyan]"]

    files_block = "\n".join(file_lines) if file_lines else ""

    main_lines = [
        f"  {phase}",
        f"  {status}{keys}",
        "",
        progress_line,
        "",
        (f"  [bold cyan]📂  {trunc(cur_dir)}[/bold cyan]"
         if cur_dir else f"  [dim cyan]{spin_frame}  finding audiobook folders …[/dim cyan]"),
        files_block,
    ]
    main_body = "\n".join(l for l in main_lines if l)

    # ── Activity log ──────────────────────────────────────────────────────────
    # Level → (icon, Rich style)
    _fmt = {
        "info":  ("→",  "cyan"),           # dir starting scan  — cyan, readable
        "good":  ("✓",  "green"),           # multi-file clean   — bright green
        "single":("♪",  "magenta"),         # single-file clean  — magenta/pink
        "cached":("⚡", "dim blue"),        # cache hit          — muted blue
        "warn":  ("⚠",  "yellow"),
        "error": ("✗",  "bold red"),
        "pause": ("⏸",  "bold yellow"),
        "debug": ("·",  "dim blue"),
    }
    log_lines = []
    for ts, lvl, msg in snap['log']:
        icon, sty = _fmt.get(lvl, ("·", "white"))
        log_lines.append(
            f"  [dim]{ts}[/dim]  [{sty}]{icon}  {msg}[/{sty}]"
        )
    log_body = "\n".join(log_lines) if log_lines else "  [dim](no activity yet)[/dim]"

    return RGroup(
        Panel(main_body, border_style="cyan", padding=(0, 1)),
        Panel(log_body,
              title="[dim] Activity Log [/dim]",
              border_style="dim", padding=(0, 1)),
    )


# ── Pure worker functions  (no console I/O — safe to call from threads) ──────

def _prescan_dir(book_dir: Path, state: ScanState = None) -> dict:
    """
    Analyse one book directory for all per-dir issues.
    Returns a plain dict the main thread can read safely.

    Cache behaviour:
      • If the directory fingerprint matches a previous clean scan → return
        a cached-clean result immediately (no ffprobe, no file reads beyond stat).
      • If issues are found → evict from cache so next run re-checks.
      • If clean → write fingerprint to cache.
    """
    rel_name    = str(book_dir.relative_to(ROOT))
    audio_files = get_audio_files(book_dir)
    has_cover   = get_cover_file(book_dir) is not None
    fmt_hint    = audio_files[0].suffix.lstrip('.').upper() if audio_files else ""

    # ── Cache hit — skip all probing ─────────────────────────────────────────
    if scan_cache.is_clean_cached(book_dir):
        if state:
            state.set_current(rel_name, "")
            state.log_entry("cached",
                f"{book_dir.name}  ({len(audio_files)} files, {fmt_hint or '?'})  [cached]")
        return {
            'audio_files': audio_files,
            'has_cover':   has_cover,
            'fmt_hint':    fmt_hint,
            'junk':        [],
            'corrupt':     [],
            'from_cache':  True,
        }

    # ── Live scan ─────────────────────────────────────────────────────────────
    if state:
        state.set_current(rel_name, "")
        nf = len(audio_files)
        state.log_entry("info",
            f"{book_dir.name}  ({nf} file{'s' if nf != 1 else ''}, {fmt_hint or '?'})")

    junk = [
        f for f in book_dir.iterdir()
        if f.is_file() and (
            f.suffix.lower() in JUNK_EXTS or
            (f.name.startswith('.') and f.name not in ('.', '..'))
        )
    ]

    corrupt = []
    if (HAS_MUTAGEN or HAS_FFMPEG) and audio_files:
        file_workers = min(NUM_WORKERS, len(audio_files), 4)
        per_file_timeout = FFPROBE_TIMEOUT + 2

        def _probe_and_track(af: Path):
            if state:
                state.file_started(af.name)
            try:
                return _check_audio_file(af)
            finally:
                if state:
                    state.file_finished(af.name)

        with ThreadPoolExecutor(max_workers=file_workers,
                                thread_name_prefix="fprobe") as file_pool:
            fut_to_file = {
                file_pool.submit(_probe_and_track, af): af
                for af in audio_files
            }
            for fut, af in fut_to_file.items():
                if state:
                    if not state.wait_if_paused():
                        for f in fut_to_file:
                            f.cancel()
                        break

                try:
                    rec = fut.result(timeout=per_file_timeout)
                except TimeoutError:
                    # concurrent.futures raises TimeoutError (not subprocess.TimeoutExpired)
                    if state:
                        state.file_finished(af.name)
                    rec = {
                        'path':  str(af),
                        'issue': f'ffprobe timed out after {per_file_timeout}s — file may be corrupt or unreadable',
                        'size':  af.stat().st_size if af.exists() else 0,
                    }
                    if state:
                        state.log_entry("warn",
                            f"⚠  Timeout ({per_file_timeout}s): {book_dir.name}/{af.name}")
                except Exception as e:
                    if state:
                        state.file_finished(af.name)
                    reason = str(e).strip() or type(e).__name__
                    rec = {
                        'path':  str(af),
                        'issue': f'Probe error: {reason}',
                        'size':  af.stat().st_size if af.exists() else 0,
                    }
                    if state:
                        state.log_entry("warn",
                            f"⚠  Probe error: {book_dir.name}/{af.name}  [{reason}]")

                if rec:
                    corrupt.append(rec)
                    if state:
                        state.log_entry("error",
                            f"✗  Corrupt: {book_dir.name}/{af.name}  [{rec['issue']}]")

    # ── Update cache ──────────────────────────────────────────────────────────
    n_issues = len(junk) + len(corrupt)
    if n_issues == 0:
        scan_cache.mark_clean(book_dir, len(audio_files), fmt_hint)
        scan_logger.clean(book_dir, len(audio_files), fmt_hint)
        if state:
            nf         = len(audio_files)
            cover_tag  = "cover ✓" if has_cover else "no cover"
            if nf == 1:
                state.log_entry("single",
                    f"{book_dir.name}  (1 file, {fmt_hint or '?'}, {cover_tag})")
            else:
                state.log_entry("good",
                    f"{book_dir.name}  ({nf} files, {fmt_hint or '?'}, {cover_tag})")
    else:
        scan_cache.mark_dirty(book_dir)
        parts = []
        if junk:    parts.append(f"{len(junk)} junk")
        if corrupt: parts.append(f"{len(corrupt)} corrupt")
        if state:
            state.log_entry("warn",
                f"{book_dir.name}  [{', '.join(parts)}]")

    if state:
        state.set_current(rel_name, "")

    return {
        'audio_files': audio_files,
        'has_cover':   has_cover,
        'fmt_hint':    fmt_hint,
        'junk':        junk,
        'corrupt':     corrupt,
        'from_cache':  False,
    }


def _fuzzy_row(args: tuple) -> tuple:
    """
    Compare one directory (index i) against all others.
    Returns (i, cluster_list, scores_dict).
    Pure function — safe for ThreadPoolExecutor.
    """
    i, dir_i, norm_i, named, threshold = args
    if not norm_i:
        return i, [dir_i], {}
    cluster = [dir_i]
    scores  = {}
    for j, (dir_j, norm_j) in enumerate(named):
        if j == i or not norm_j:
            continue
        sc = fuzzy_score(norm_i, norm_j)
        if sc >= threshold:
            cluster.append(dir_j)
            scores[id(dir_j)] = sc
    return i, cluster, scores


# ── Main scanner ─────────────────────────────────────────────────────────────

def sequential_scan():
    banner("Sequential Issue Scanner")
    print()

    # ── Discover book directories ─────────────────────────────────────────────
    if HAS_RICH:
        console.print("  [dim]Discovering audiobook directories …[/dim]",
                      end='\r', highlight=False)
    else:
        print("  Discovering audiobook directories …", end='\r', flush=True)

    book_dirs = find_book_dirs()
    print()

    if not book_dirs:
        warn("No audiobook directories found in the current directory.")
        return

    total_dirs = len(book_dirs)

    # ── Evict cache entries for directories that no longer exist ──────────────
    evicted = scan_cache.evict_missing(book_dirs)

    cached_clean = sum(1 for d in book_dirs if scan_cache.is_clean_cached(d))
    to_scan      = total_dirs - cached_clean

    good(f"Found {total_dirs} audiobook folder{'s' if total_dirs != 1 else ''} to check.")
    if cached_clean:
        if HAS_RICH:
            console.print(
                f"  [dim][green]{cached_clean} folder{'s' if cached_clean != 1 else ''} already verified clean[/green]"
                f" — skipping.  [cyan]{to_scan} folder{'s' if to_scan != 1 else ''} will be scanned.[/cyan]"
                + (f"  ({evicted} removed from cache — no longer exist)" if evicted else "")
                + "[/dim]"
            )
        else:
            print(f"  {cached_clean} folders already verified clean — skipping.  "
                  f"{to_scan} folders will be scanned."
                  + (f"  ({evicted} removed from cache)" if evicted else ""))
    else:
        if HAS_RICH:
            console.print(f"  [dim]First run — all {total_dirs} folders will be scanned.[/dim]")
        else:
            print(f"  First run — all {total_dirs} folders will be scanned.")

    scan_logger.session_start(total_dirs, scan_cache.size)

    if not HAS_RICH:
        info(f"Using {NUM_WORKERS} worker thread(s).")
        print()

    resolved       = 0
    skipped        = 0
    issue_num      = 0
    scanned_count  = 0   # dirs actually probed (not from cache)
    multidisc_seen: set = set()

    state = ScanState()
    state.total = total_dirs

    # ── Plain-text fallback (no Rich) ─────────────────────────────────────────
    if not HAS_RICH:
        print("  Step 1 of 2  —  Checking each audiobook folder for issues")
        print("  Looking for: junk files, multi-disc splits, corrupt audio")
        print()

        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
            futures = [pool.submit(_prescan_dir, d, state) for d in book_dirs]

            for dir_idx, (book_dir, future) in enumerate(zip(book_dirs, futures), 1):
                if state.is_aborted(): future.cancel(); continue
                try:
                    scan = future.result()
                    state.advance()
                except Exception as e:
                    bad(f"Error scanning '{book_dir.name}': {e}"); continue

                rel_name = str(book_dir.relative_to(ROOT))
                if not scan.get('from_cache'):
                    scanned_count += 1
                _dir_status_line(dir_idx, total_dirs, rel_name,
                                 len(scan['audio_files']), scan['has_cover'],
                                 scan['fmt_hint'])

                for iss in _issues_for_dir(book_dir, scan, rel_name,
                                           multidisc_seen, state):
                    if state.is_aborted(): break
                    scan_logger.issue(book_dir, iss.category, iss.summary)
                    issue_num += 1
                    r, s, quit_flag = _run_issue(iss, issue_num, resolved, skipped)
                    resolved += r; skipped += s
                    if r:   scan_logger.resolved(iss.category, iss.summary)
                    else:   scan_logger.skipped_issue(iss.category, iss.summary)
                    if quit_flag: state.abort.set(); state.paused.set(); break

        if state.is_aborted():
            print()
            info(f"Stopped.  Resolved: {resolved}  Skipped: {skipped}  "
                 f"Issues so far: {issue_num}")
            return

        # Phase 2 plain-text
        _run_dup_phase_plain(state, resolved, skipped, issue_num)
        return

    # ── Rich path — Live display with activity log ────────────────────────────
    _start_keyboard_thread(state)
    phase1 = "Step [bold]1[/bold] of 2  [dim]—  Checking each audiobook folder for issues[/dim]"
    phase2 = "Step [bold]2[/bold] of 2  [dim]—  Scanning for duplicate audiobooks[/dim]"

    console.print(f"  [dim]Using {NUM_WORKERS} parallel workers. "
                  f"Press [bold]P[/bold] to pause, [bold]Q[/bold] to abort.[/dim]")
    print()

    with Live(_make_live_display(state, phase1),
              refresh_per_second=12,
              console=console,
              transient=False) as live:

        _current_phase = [phase1]   # mutable box so the thread closure can read updates

        def _refresh():
            live.update(_make_live_display(state, _current_phase[0]))

        # ── Background refresh thread — animates spinner at 12 fps ───────────
        # The main thread blocks on future.result() during heavy ffprobe work,
        # so without this thread the display would freeze between completions.
        _stop_refresh = threading.Event()
        def _refresh_loop():
            import time as _t
            while not _stop_refresh.is_set():
                try:
                    _refresh()
                except Exception:
                    pass
                _t.sleep(1 / 12)
        _rt = threading.Thread(target=_refresh_loop, daemon=True, name="live-refresh")
        _rt.start()

        # ── Phase 1 ───────────────────────────────────────────────────────────
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
            futures = [pool.submit(_prescan_dir, d, state) for d in book_dirs]

            for dir_idx, (book_dir, future) in enumerate(zip(book_dirs, futures), 1):
                if state.is_aborted():
                    future.cancel()
                    continue

                try:
                    scan = future.result()
                    state.advance()
                    _refresh()
                except Exception as e:
                    state.log_entry("error",
                        f"Error scanning '{book_dir.name}': {e}")
                    state.advance()
                    _refresh()
                    continue

                rel_name = str(book_dir.relative_to(ROOT))
                if not scan.get('from_cache'):
                    scanned_count += 1

                for iss in _issues_for_dir(book_dir, scan, rel_name,
                                           multidisc_seen, state):
                    if state.is_aborted(): break
                    scan_logger.issue(book_dir, iss.category, iss.summary)
                    issue_num += 1
                    _stop_refresh.set()
                    live.stop()
                    r, s, quit_flag = _run_issue(iss, issue_num, resolved, skipped)
                    resolved += r; skipped += s
                    if r:   scan_logger.resolved(iss.category, iss.summary)
                    else:   scan_logger.skipped_issue(iss.category, iss.summary)
                    if quit_flag:
                        state.abort.set()
                        state.paused.set()
                    if not state.is_aborted():
                        _stop_refresh.clear()
                        threading.Thread(target=_refresh_loop, daemon=True,
                                         name="live-refresh-p1").start()
                        live.start()

        if state.is_aborted():
            _stop_refresh.set()
            live.stop()
            print()
            info(f"Stopped at directory {state.done}/{total_dirs}.  "
                 f"Resolved: {resolved}  Skipped: {skipped}  "
                 f"Issues so far: {issue_num}")
            return

        # ── Phase 2 — duplicate detection ────────────────────────────────────
        book_dirs2 = find_book_dirs()
        named      = [(d, normalize_name(d.name)) for d in book_dirs2]
        total_cmp  = len(named)

        state.done  = 0
        state.total = total_cmp
        _current_phase[0] = phase2
        state.log_entry("info",
            f"Now checking for duplicates across {total_cmp} audiobook folders …")

        row_args = [(i, d, n, named, _DUP_THRESHOLD)
                    for i, (d, n) in enumerate(named)]

        all_rows = [None] * total_cmp
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
            fut_map = {pool.submit(_fuzzy_row, a): a[0] for a in row_args}
            for fut in as_completed(fut_map):
                if state.is_aborted():
                    break
                idx = fut_map[fut]
                all_rows[idx] = fut.result()
                state.advance()
                dir_name = named[idx][0].name
                state.set_current(dir_name, "")

        state.set_current("", "")

        if state.is_aborted():
            _stop_refresh.set()
            live.stop()
            info(f"Stopped during duplicate detection.  "
                 f"Resolved: {resolved}  Skipped: {skipped}")
            return

        # Deduplicate and present
        used: set = set()
        for row in all_rows:
            if row is None or state.is_aborted():
                continue
            i, cluster, scores = row
            dir_i = named[i][0]
            if id(dir_i) in used: continue
            cluster = [d for d in cluster if id(d) not in used]
            if len(cluster) < 2: continue
            for d in cluster: used.add(id(d))

            label = " / ".join(d.name[:26] for d in cluster[:3])
            if len(cluster) > 3: label += f"  +{len(cluster)-3} more"
            issue_num += 1

            state.log_entry("warn",
                f"Duplicate group ({len(cluster)} dirs): {label[:50]}")

            def _mk_dup(c, sc):
                def _resolve(_):
                    _resolve_duplicate_group(c, sc)
                    return True
                return _resolve

            iss = Issue(
                category   = "Duplicate Book",
                summary    = f"{len(cluster)} likely-duplicate dirs: {label}",
                detail     = (f"{len(cluster)} directories appear to contain the same "
                              f"audiobook at different qualities or from different sources.\n"
                              f"  {label}"),
                path       = cluster[0],
                severity   = "warning",
                resolve_fn = _mk_dup(cluster, scores),
            )
            _stop_refresh.set()
            live.stop()
            r, s, quit_flag = _run_issue(iss, issue_num, resolved, skipped)
            resolved += r; skipped += s
            if quit_flag:
                state.abort.set(); state.paused.set()
            if not state.is_aborted():
                _stop_refresh.clear()
                _rt2 = threading.Thread(target=_refresh_loop, daemon=True, name="live-refresh2")
                _rt2.start()
                live.start()

        _stop_refresh.set()
        live.stop()

    print()
    banner("Sequential Scan Complete")
    if issue_num == 0:
        good("✨  No issues found — your library is clean!")
    else:
        good(f"Issues found: {issue_num}   "
             f"Resolved: {resolved}   Skipped: {skipped}")

    cached_skipped = total_dirs - scanned_count
    if HAS_RICH:
        console.print(
            f"  [dim]Scanned: {scanned_count}  "
            f"Skipped (cached clean): {cached_skipped}  "
            f"Cache size: {scan_cache.size} entries[/dim]"
        )
    else:
        print(f"  Scanned: {scanned_count}  "
              f"Skipped (cached clean): {cached_skipped}  "
              f"Cache size: {scan_cache.size} entries")

    scan_logger.session_end(scanned_count, cached_skipped, issue_num, resolved)
    scan_logger.flush()
    scan_cache.save()
    if HAS_RICH:
        console.print(f"  [dim]Cache saved → {CACHE_FILE.name}  "
                      f"Log appended → {LOG_FILE.name}[/dim]")
    else:
        print(f"  Cache saved → {CACHE_FILE.name}  "
              f"Log appended → {LOG_FILE.name}")


# ── Helpers extracted to keep sequential_scan readable ───────────────────────

def _issues_for_dir(book_dir: Path, scan: dict, rel_name: str,
                    multidisc_seen: set, state: ScanState) -> list:
    """
    Build the list of Issue objects for one directory from a pre-scan result.
    Returns issues in severity order: corrupt first, then multi-disc, then junk.
    """
    issues = []

    # Corrupt files (severity: error)
    for rec in scan['corrupt']:
        def _mk_corrupt(r):
            def _resolve(_):
                return _run_corrupt_repair(Path(r['path']), r)
            return _resolve
        af = Path(rec['path'])
        issues.append(Issue(
            category   = "Corrupt / Truncated",
            summary    = f"{af.name} — {rec['issue']}",
            detail     = (f"'{af.name}' failed audio health check.\n"
                          f"  {rec['issue']}\n"
                          f"  Size: {fmt_size(rec['size'])}"),
            path       = af,
            severity   = "error",
            resolve_fn = _mk_corrupt(dict(rec)),
        ))

    # Multi-disc structure (severity: warning)
    result = _check_dir_multidisc(book_dir, multidisc_seen)
    if result:
        parent, disc_subs = result
        multidisc_seen.add(parent)
        for d in disc_subs: multidisc_seen.add(d)
        total_files = sum(len(get_audio_files(d)) for d in disc_subs)
        disc_labels = "  ·  ".join(d.name for d in disc_subs[:4])
        if len(disc_subs) > 4: disc_labels += f"  +{len(disc_subs)-4} more"

        def _mk_disc(p, ds):
            def _resolve(_): return consolidate_multidisc_book(p, ds)
            return _resolve
        issues.append(Issue(
            category   = "Multi-Disc Book",
            summary    = f"'{parent.name}' — {len(disc_subs)} disc sub-folders",
            detail     = (f"'{parent.name}' contains {len(disc_subs)} sub-directories "
                          f"that appear to be disc/part splits ({total_files} total files).\n"
                          f"  Discs: {disc_labels}\n"
                          f"  These can be consolidated into a single flat directory."),
            path       = parent,
            severity   = "warning",
            resolve_fn = _mk_disc(parent, disc_subs),
        ))

    # Junk files (severity: info)
    if scan['junk']:
        junk = scan['junk']
        exts = sorted({f.suffix.lower() or f.name for f in junk})
        sz   = fmt_size(sum(f.stat().st_size for f in junk))

        def _mk_junk(flist, drel):
            def _resolve(_):
                h2(f"Junk files in: {drel}")
                for f in flist:
                    dim(f"    {f.name}  ({fmt_size(f.stat().st_size)})")
                print()
                if not prompt_confirm(f"Delete all {len(flist)} junk file(s)?"):
                    info("Skipped.")
                    return False
                n = 0
                for f in flist:
                    try:   f.unlink(); n += 1
                    except Exception as e: bad(f"Could not delete {f.name}: {e}")
                good(f"Deleted {n} file(s).")
                return True
            return _resolve
        issues.append(Issue(
            category   = "Junk Files",
            summary    = f"{len(junk)} junk file(s) in '{rel_name}'",
            detail     = (f"'{rel_name}' has {len(junk)} non-audio file(s) "
                          f"({', '.join(exts)}, {sz}) with no use in an audiobook server."),
            path       = book_dir,
            severity   = "info",
            resolve_fn = _mk_junk(list(junk), rel_name),
        ))

    return issues


def _run_dup_phase_plain(state, resolved, skipped, issue_num):
    """Plain-text duplicate phase for when Rich is unavailable."""
    book_dirs2 = find_book_dirs()
    named      = [(d, normalize_name(d.name)) for d in book_dirs2]
    total_cmp  = len(named)
    print(f"\n  Step 2 of 2  —  Scanning for duplicate audiobooks  "
          f"(threshold: {_DUP_THRESHOLD}%,  {NUM_WORKERS} workers)")
    print(f"  Comparing {total_cmp} directories "
          f"({total_cmp*(total_cmp-1)//2} pairs) …")

    row_args = [(i, d, n, named, _DUP_THRESHOLD) for i, (d, n) in enumerate(named)]
    print("  Running …", end=' ', flush=True)
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
        all_rows = list(pool.map(_fuzzy_row, row_args))
    print("done.\n")

    used: set = set()
    for row in all_rows:
        i, cluster, scores = row
        dir_i = named[i][0]
        if id(dir_i) in used: continue
        cluster = [d for d in cluster if id(d) not in used]
        if len(cluster) < 2: continue
        for d in cluster: used.add(id(d))
        label = " / ".join(d.name[:26] for d in cluster[:3])
        if len(cluster) > 3: label += f"  +{len(cluster)-3} more"
        issue_num += 1
        _dup_status_line(i + 1, total_cmp, dir_i.name, total_cmp - i - 1)

        def _mk_dup(c, sc):
            def _resolve(_):
                _resolve_duplicate_group(c, sc)
                return True
            return _resolve
        iss = Issue(
            category   = "Duplicate Book",
            summary    = f"{len(cluster)} likely-duplicate dirs: {label}",
            detail     = (f"{len(cluster)} directories appear to contain the same "
                          f"audiobook at different qualities or sources.\n  {label}"),
            path       = cluster[0],
            severity   = "warning",
            resolve_fn = _mk_dup(cluster, scores),
        )
        r, s, quit_flag = _run_issue(iss, issue_num, resolved, skipped)
        resolved += r; skipped += s
        if quit_flag: break

    print()
    banner("Sequential Scan Complete")
    if issue_num == 0:
        good("✨  No issues found — your library is clean!")
    else:
        good(f"Issues found: {issue_num}   Resolved: {resolved}   Skipped: {skipped}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main menu
# ─────────────────────────────────────────────────────────────────────────────

MENU_ITEMS = [
    ("🔍  Sequential Issue Scanner  [recommended starting point]", sequential_scan),
    (None, None),   # visual separator
    ("🗑   Clean Junk Files",                          clean_junk_files),
    ("🔎  Duplicate Detection & Removal",              find_duplicates),
    ("💿  Multi-Disc Detection & Consolidation",       scan_multidisc),
    ("📁  Folder Rename / Standardization",            standardize_folders),
    ("📊  Library Health HTML Report",                 generate_health_report),
    ("🩺  Corrupt / Truncated File Scan",              detect_corrupt_files),
]

def main():
    if HAS_RICH:
        console.print(Panel.fit(
            f"[bold cyan]Audiobook Library Manager[/bold cyan]  v2.0\n"
            f"[dim]Library root: {ROOT}[/dim]",
            border_style="cyan", padding=(0, 2)
        ))
    else:
        print("\n" + "═"*62)
        print("  Audiobook Library Manager  v2.0")
        print(f"  Library root: {ROOT}")
        print("═"*62)

    print()
    _ffmpeg_startup_notice()
    if not HAS_MUTAGEN and not HAS_FFMPEG:
        warn("Neither mutagen nor FFmpeg found — audio analysis disabled.")
    if not HAS_FUZZY:
        warn("rapidfuzz not available — using difflib (slower matching).")

    callables = [(label, fn) for label, fn in MENU_ITEMS if fn is not None]

    while True:
        print()
        if HAS_RICH: console.print("  [bold white]Main Menu[/bold white]")
        else:        print("  Main Menu")
        print()

        n = 1
        for label, fn in MENU_ITEMS:
            if fn is None:
                dim(f"  {'─'*50}")
            else:
                print(f"    [{n}]  {label}")
                n += 1
        print("    [0]  Exit\n")

        raw = prompt_text("Select option")
        if raw.strip() == '0':
            good("Goodbye.")
            break
        try:
            choice = int(raw.strip())
            if 1 <= choice <= len(callables):
                print()
                try:
                    callables[choice-1][1]()
                except KeyboardInterrupt:
                    print()
                    warn("Interrupted — returning to menu.")
            else:
                warn("Invalid option.")
        except ValueError:
            warn("Please enter a number.")


if __name__ == '__main__':
    main()