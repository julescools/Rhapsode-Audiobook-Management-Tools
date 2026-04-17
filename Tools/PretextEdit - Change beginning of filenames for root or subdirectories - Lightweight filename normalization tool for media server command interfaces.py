# PretextEdit.py
import os
import random
import sys
import threading

# ── Progress bar ─────────────────────────────────────────────────────────────

class ProgressBar:
    def __init__(self, total, width=45):
        self.total = total
        self.width = width
        self.current = 0
        self._lock = threading.Lock()

    def update(self, label=""):
        with self._lock:
            self.current += 1
            pct = self.current / self.total if self.total else 1
            filled = int(self.width * pct)
            bar = "█" * filled + "░" * (self.width - filled)
            label_str = label[:40].ljust(40)
            sys.stdout.write(f"\r  [{bar}] {self.current}/{self.total}  {label_str}")
            sys.stdout.flush()
            if self.current >= self.total:
                sys.stdout.write("\n")
                sys.stdout.flush()


# ── File helpers ──────────────────────────────────────────────────────────────

def get_files_in_folder(folder):
    try:
        return [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
    except PermissionError:
        return []


def get_subdirs(folder):
    try:
        return sorted([d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))])
    except PermissionError:
        return []


def get_local_files(folder, script_name):
    return [f for f in get_files_in_folder(folder) if f != script_name]


def split_by_prefix(files, prefix):
    """Separate files into those that need renaming vs already prefixed."""
    already = [f for f in files if f.startswith(prefix)]
    needs   = [f for f in files if not f.startswith(prefix)]
    return needs, already


# ── Preview helpers ───────────────────────────────────────────────────────────

def show_local_preview(files, prefix):
    needs, already = split_by_prefix(files, prefix)
    sample = random.sample(needs, min(10, len(needs)))

    print(f"\n  --- Preview ({len(sample)} of {len(needs)} file(s) will be renamed) ---")
    for f in sample:
        print(f"    {f}  →  {prefix + f}")

    if already:
        print(f"\n  ℹ  {len(already)} file(s) already have this prefix and will be skipped:")
        for f in already[:5]:
            print(f"    • {f}")
        if len(already) > 5:
            print(f"    ... and {len(already) - 5} more.")
    print("  ---")
    return needs, already


def show_subdir_preview(folder, subdirs, prefix):
    """Show 3 sample renames per subdir, noting already-prefixed files."""
    grand_needs = 0
    grand_skip  = 0

    for d in subdirs:
        path  = os.path.join(folder, d)
        files = get_files_in_folder(path)
        needs, already = split_by_prefix(files, prefix)
        grand_needs += len(needs)
        grand_skip  += len(already)

        sample = random.sample(needs, min(3, len(needs)))

        status_parts = []
        if needs:
            status_parts.append(f"{len(needs)} to rename")
        if already:
            status_parts.append(f"{len(already)} already prefixed")
        if not needs and not already:
            status_parts.append("empty")

        print(f"\n  📁  {d}/  ({', '.join(status_parts)})")

        if sample:
            for f in sample:
                print(f"      {f}  →  {prefix + f}")

        if not needs and already:
            print(f"      ✔ All files already have this prefix — folder will be skipped.")
        elif already:
            print(f"      ℹ  {len(already)} file(s) already prefixed, will be skipped.")

    return grand_needs, grand_skip


# ── Mode: local files ─────────────────────────────────────────────────────────

def mode_local(folder, script_name):
    files = get_local_files(folder, script_name)
    if not files:
        print("\n  No files found in this folder.")
        return

    while True:
        prefix = input("\n  Enter prefix text: ")
        if not prefix.strip():
            print("  No text entered. Try again.")
            continue

        needs, already = show_local_preview(files, prefix)

        if not needs:
            print("\n  ✔ All files already have this prefix. Nothing to rename.")
            return

        print("\n  [1] Execute   [2] New text   [3] Cancel")
        choice = input("  Choice: ").strip()

        if choice == "1":
            bar = ProgressBar(len(needs))
            print()
            renamed = 0
            for f in needs:
                try:
                    os.rename(os.path.join(folder, f), os.path.join(folder, prefix + f))
                    renamed += 1
                except OSError as e:
                    print(f"\n  ⚠  Could not rename '{f}': {e}")
                bar.update(label=f)

            print(f"  ✔  Done. {renamed} file(s) renamed.", end="")
            if already:
                print(f"  {len(already)} already had the prefix and were skipped.", end="")
            print()
            return

        elif choice == "2":
            continue
        elif choice == "3":
            print("  Cancelled.")
            return
        else:
            print("  Invalid choice.")


# ── Mode: subdirectories ──────────────────────────────────────────────────────

def select_subdirs(folder, subdirs):
    print()
    for i, d in enumerate(subdirs, 1):
        count = len(get_files_in_folder(os.path.join(folder, d)))
        print(f"  [{i:>2}] {d}/  ({count} file(s))")

    print("\n  Enter folder numbers (e.g. 1,3,5) or press Enter for ALL:")
    raw = input("  Selection: ").strip()

    if not raw:
        return subdirs

    chosen = []
    for part in raw.replace(" ", "").split(","):
        try:
            idx = int(part) - 1
            if 0 <= idx < len(subdirs):
                chosen.append(subdirs[idx])
            else:
                print(f"  ⚠  '{part}' out of range, skipped.")
        except ValueError:
            print(f"  ⚠  '{part}' is not a number, skipped.")

    return chosen


def mode_subdirs(folder):
    subdirs = get_subdirs(folder)
    if not subdirs:
        print("\n  No subdirectories found.")
        return

    print(f"\n  Found {len(subdirs)} subdirectory/ies.")
    chosen = select_subdirs(folder, subdirs)

    if not chosen:
        print("  No valid folders selected.")
        return

    while True:
        prefix = input("\n  Enter prefix text: ")
        if not prefix.strip():
            print("  No text entered. Try again.")
            continue

        print(f"\n  Preview for {len(chosen)} folder(s):")
        grand_needs, grand_skip = show_subdir_preview(folder, chosen, prefix)

        print(f"\n  Total to rename: {grand_needs}   |   Already prefixed (will skip): {grand_skip}")

        if grand_needs == 0:
            print("  ✔ All files across selected folders already have this prefix. Nothing to do.")
            return

        print("\n  [1] Execute   [2] New text   [3] Pick different folders   [4] Cancel")
        choice = input("  Choice: ").strip()

        if choice == "1":
            bar = ProgressBar(grand_needs)
            print()
            renamed  = 0
            skipped  = 0
            for d in chosen:
                path  = os.path.join(folder, d)
                files = get_files_in_folder(path)
                needs, already = split_by_prefix(files, prefix)
                skipped += len(already)
                for f in needs:
                    try:
                        os.rename(os.path.join(path, f), os.path.join(path, prefix + f))
                        renamed += 1
                    except OSError as e:
                        print(f"\n  ⚠  Could not rename '{f}' in '{d}': {e}")
                    bar.update(label=f"{d}/{f}")

            print(f"  ✔  Done. {renamed} file(s) renamed across {len(chosen)} folder(s).", end="")
            if skipped:
                print(f"  {skipped} already had the prefix and were skipped.", end="")
            print()
            return

        elif choice == "2":
            continue
        elif choice == "3":
            chosen = select_subdirs(folder, subdirs)
            if not chosen:
                print("  No valid folders selected.")
        elif choice == "4":
            print("  Cancelled.")
            return
        else:
            print("  Invalid choice.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    script_name = os.path.basename(__file__)
    folder      = os.path.dirname(os.path.abspath(__file__))

    print("╔══════════════════════════════╗")
    print("║       PretextEdit  v2.1      ║")
    print("╚══════════════════════════════╝")

    while True:
        local_count  = len(get_local_files(folder, script_name))
        subdir_count = len(get_subdirs(folder))

        print(f"\n  Folder: {folder}")
        print(f"  Local files: {local_count}   |   Subdirectories: {subdir_count}")

        print("\n  Main Menu:")
        print("  [1] Add prefix to files in THIS folder")
        print("  [2] Add prefix to files inside subdirectories")
        print("  [3] Refresh")
        print("  [4] Exit")
        choice = input("\n  Choice: ").strip()

        if choice == "1":
            mode_local(folder, script_name)
        elif choice == "2":
            mode_subdirs(folder)
        elif choice == "3":
            print("  Refreshed.")
        elif choice == "4":
            print("\n  Goodbye.\n")
            break
        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    main()
