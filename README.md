# 🎙️ Rhapsode Audiobook File Manager
> *An audiobook toolbox launcher designed specifically to help with managing Audiobookshelf self-hosted service.*
>
> This is a collection of tools I wrote to help me manage audiobook files.  In the process of ripping your collection or downloading files, often times the formatting is at odds with the expectations of services like Audiobookshelf, which is what I use.  Hopefully you find these tools helpful in renaming, splitting, repairing, organizing, etc.  I am working on a main app that pulls the tools all into one working tool, but in the meantime all tools in the Tools folder are fully functional with detailed user prompts, file summaries, progress bars, error handling, and much more.  Everything I found useful to get my library in working order.
---

## ✨ What it does

A single-file Python launcher that puts a friendly menu in front of a dozen standalone audiobook-management scripts. Designed for people who maintain their own library (Audiobookshelf, Plex, a shelf of ripped CDs) and find themselves doing the same handful of cleanup tasks over and over.

- 🔪 **Split & extract** — slice long audio files into equal chunks, extract segments by time, split m4b files by chapter
- 🧵 **Join** — merge Libation-style chapter files (and cover art) into a single audiobook
- 📂 **Organize** — flatten multi-disc folder structures into clean flat libraries
- ✏️ **Rename** — add prefixes, strip junk, collapse Part/Chapter/Subchapter numbering
- 🧹 **Metadata & repair** — clear ID3 tags, re-encode corrupt MP3s
- 📚 **Library management** — full scanner/fixer for an Audiobookshelf root

Every tool is standalone and can be run directly. Rhapsode just provides a menu.

---

## 📋 Requirements

### 🟥 Required

| | Requirement | Used by |
|---|---|---|
| 🐍 | **Python 3.8 or later** | Launcher + every `.py` tool |
| 🎬 | **ffmpeg + ffprobe** on your PATH | Any tool that touches audio |

### 🟦 Optional Python packages

Only needed for the specific tools that use them:

| Package | Install | Used by |
|---|---|---|
| `mutagen` | `pip install mutagen` | `clear_mp3_metadata.py`, `audiobook_manager.py` |
| `rapidfuzz` | `pip install rapidfuzz` | `audiobook_manager.py` |
| `rich` | `pip install rich` | `audiobook_manager.py` |

> 💡 **You don't actually have to pre-install these.** `audiobook_manager.py` auto-prompts to install its own dependencies on first run.

---

## 🚀 Installation

### 1️⃣ Python

| Platform | Command |
|---|---|
| 🪟 Windows | [python.org installer](https://www.python.org/downloads/) (check **Add to PATH**) or `winget install Python.Python.3` |
| 🍎 macOS | `brew install python` |
| 🐧 Linux | `sudo apt install python3` (or your distro's equivalent) |

Verify:
```bash
python --version
```

### 2️⃣ ffmpeg

The engine behind almost every audio operation. You need both `ffmpeg` **and** `ffprobe` on your PATH.

```bash
# 🪟 Windows
winget install ffmpeg

# 🍎 macOS
brew install ffmpeg

# 🐧 Ubuntu / Debian
sudo apt install ffmpeg

# 🐧 Arch
sudo pacman -S ffmpeg
```

Verify:
```bash
ffmpeg -version
ffprobe -version
```

### 3️⃣ Python packages (one-shot, optional)

```bash
pip install mutagen rapidfuzz rich
```

Or use:
```bash
python -m pip install mutagen rapidfuzz rich
```

### 4️⃣ Clone the repo

```bash
git clone https://github.com/julescools/rhapsode.git
cd rhapsode
```

### 5️⃣ Run

```bash
python rhapsode.py
```

🎉 Done.

---

## 📁 Folder structure

```
rhapsode/
├── 📜 rhapsode.py          ← the launcher (sits here)
└── 📁 Tools/               ← all the workhorse scripts
    ├── audiobook_manager.py
    ├── audiobook_splitter_extraction_tool.py
    ├── extract_m4b_chapter_audio_files.py
    ├── join_libation_m4b_files_..._.py
    ├── combine_audio_ffmpeg.bat
    ├── audiobook_take_all_files_..._.py
    ├── PretextEdit_..._.py
    ├── rename_part_chapter_subchapter_..._.py
    ├── remove_first_space.bat
    ├── clear_mp3_metadata.py
    └── repair_mp3.py
```

🔍 Rhapsode discovers tools by **exact filename**. If you rename a file in `Tools/`, update the matching `filename=` entry in the `CATEGORIES` list near the top of `rhapsode.py`.

---

## 🎯 Usage

### Quick start

```bash
cd "/path/to/my/audiobook"
python /path/to/rhapsode/rhapsode.py
```

By default, the **target directory** (where the tools operate) is your current working directory — usually the book you're working on. Change it at any time with `d`.

### Menu controls

| Key | Action |
|:---:|---|
| `1` – `N` | ▶️ Run a tool |
| `d` | 📂 Change target directory |
| `i` | ℹ️ Show details about a tool |
| `s` | 📋 Status — list all tools, flag any missing files |
| `q` | 🚪 Quit |
| `Ctrl+C` | ⏹ Interrupt a running tool and return to the menu |

---

## 🧰 The toolbox

### 📚 Library
- **Audiobook Library Manager** — full scanner/fixer for an Audiobookshelf root. Scan cache, corrupt-file detection, batch renames, fuzzy-match cleanup. This is the big one.

### 🔪 Split & Extract
- **Slice or extract segment** — split one file into equal chunks, extract a segment by start/end time, or split by chapter markers. Uses `-c copy` → no re-encoding, no quality loss.
- **Extract m4b chapters (parallel)** — for each `.m4b` in the target dir, extract every chapter into its own file, multi-threaded with live progress.

### 🧵 Join
- **Join Libation m4b parts + cover** — merge all `.m4b` files in a directory into one audiobook, preserving chapter markers and embedding `cover.jpg`.
- **Combine audio files (batch, Windows)** — ffmpeg concat example in a `.bat`. ⚠️ Currently hardcoded to one specific trilogy — edit before use.

### 📂 Organize
- **Flatten multi-disc audiobook folders** — take `Disc 1 / Disc 2 / CD3 / …` subfolders and collapse them into a single flat directory with renumbered tracks. Includes interactive disc-order review before anything is renamed.
- **Flatten multi-disc video folders** — same logic, for video.

### ✏️ Rename
- **PretextEdit** — add a prefix to every file in the target directory, or across multiple selected subdirectories. Live preview of the first N renames before committing.
- **Part/Chapter/Subchapter → sequential** — collapse nested numbering like `Title 01 - 02 - 03.mp3` into flat `Title - 001.mp3`. (Originally written for *Lolita*; edit the regex for other titles.)
- **Strip text before first space** (Windows) — removes everything up to the first space from every filename. Useful for stripping indexing junk.

### 🧹 Metadata & Repair
- **Clear all MP3 metadata** — strips every ID3 tag from every `.mp3` in the target dir.
- **Repair MP3 (re-encode)** — re-encodes every `.mp3` through ffmpeg to fix header/frame corruption. Keeps `.backup` files alongside.

---

## 🧠 How dispatch works (briefly)

Most tools use `Path.cwd()` to find their working directory, so Rhapsode just runs them as subprocesses with `cwd=target_dir`.

A few tools (`PretextEdit`, `clear_mp3_metadata`, `join_libation`, `video` flattener) resolve their working dir via `__file__` — for those, Rhapsode briefly copies the script into the target directory as `_rhapsode_<name>`, runs it, and deletes the copy when it finishes.

🔒 **No tool in `Tools/` is ever modified.** The launcher is a dispatcher, not a rewrite. Each script can still be run standalone by `cd`-ing into a directory and invoking it directly.

---

## 🐛 Troubleshooting

<details>
<summary><b>❌ "ffmpeg not found"</b></summary>

ffmpeg or ffprobe isn't on your PATH. Install it (see above) and then **close and reopen your terminal**. On Windows, occasionally you need to log out and back in.
</details>

<details>
<summary><b>❓ A tool shows up as (missing) in the status list</b></summary>

The filename in `Tools/` doesn't exactly match the `filename=` entry in `CATEGORIES` in `rhapsode.py`. Filenames are case-sensitive on macOS and Linux.
</details>

<details>
<summary><b>⚠️ "A previous temp copy already exists"</b></summary>

Rhapsode didn't clean up its temp copy from a previous crashed run. Look for `_rhapsode_*` in your target directory and delete it manually, then retry.
</details>

<details>
<summary><b>💥 UnicodeEncodeError on Windows with non-ASCII filenames</b></summary>

Your console code page isn't UTF-8. The launcher reconfigures stdout/stderr automatically on modern Python, but if you hit this, run:
```powershell
chcp 65001
```
before launching.
</details>

<details>
<summary><b>🔧 A tool needs a pip package I don't have</b></summary>

```bash
pip install mutagen rapidfuzz rich
```
That's the full list across every tool in the toolbox.
</details>

---

## 📝 Design notes

- 🪶 **No tool is modified.** Rhapsode is a dispatcher. Every script in `Tools/` can still be run standalone.
- 🎚️ **`-c copy` everywhere possible.** The extract/split/join tools avoid re-encoding by default — you keep your source bitrate, tags, and cover art.
- 🔤 **Windows console safety.** Stdout/stderr get reconfigured to UTF-8 on Windows to avoid the encoding crashes that show up with some Unicode filenames.
- 🧩 **Adding your own tool.** Drop a script into `Tools/`, then add a `Tool(...)` entry to the right category in `CATEGORIES` at the top of `rhapsode.py`. Set `needs_copy=True` if your script uses `__file__` to resolve its working directory.

---

## 📜 License

MIT. Do whatever.

---

<p align="center">
<sub>Built for people with way too many audiobooks.</sub><br>
<sub>📚🎧</sub>
</p>
