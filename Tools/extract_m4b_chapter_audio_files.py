#!/usr/bin/env python3
import subprocess
import json
import re
import os
import sys
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue

def clean_filename(filename):
    """Clean filename for Windows compatibility while preserving as much as possible"""
    invalid_chars = '<>:"|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '')
    filename = re.sub(r'\s+', ' ', filename)
    filename = filename.strip('. ')
    return filename

def format_time(seconds):
    """Format seconds into human readable time"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:.0f}m {secs:.0f}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours:.0f}h {minutes:.0f}m"

def format_size(bytes_size):
    """Format bytes into human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f}{unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f}TB"

def create_progress_bar(progress, width=20):
    """Create a visual progress bar"""
    filled = int(width * progress / 100)
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}]"

class CleanProgressTracker:
    def __init__(self, total_chapters, max_workers):
        self.total_chapters = total_chapters
        self.max_workers = max_workers
        self.completed = 0
        self.active_slots = [None] * max_workers  # Track what's in each slot
        self.lock = threading.Lock()
        self.start_time = time.time()
        self.display_thread = None
        self.stop_display = threading.Event()
        self.start_display()
    
    def start_display(self):
        """Start the display update thread"""
        self.display_thread = threading.Thread(target=self._display_loop)
        self.display_thread.daemon = True
        self.display_thread.start()
    
    def _display_loop(self):
        """Continuously update the display"""
        while not self.stop_display.is_set():
            self._update_display()
            time.sleep(1)
    
    def _update_display(self):
        """Update the console display"""
        with self.lock:
            # Clear the display area
            print('\r' + '\n' * (self.max_workers + 3), end='')
            print('\r' + '\033[{}A'.format(self.max_workers + 3), end='')  # Move cursor up
            
            # Show active extractions
            for i, slot in enumerate(self.active_slots):
                if slot:
                    filename = slot['filename'][:35] + '...' if len(slot['filename']) > 35 else slot['filename']
                    size = format_size(slot['size'])
                    elapsed = time.time() - slot['start_time']
                    
                    # Estimate progress based on time (rough)
                    estimated_duration = slot.get('estimated_duration', 60)  # Default 1 min
                    progress = min(100, (elapsed / estimated_duration) * 100) if estimated_duration > 0 else 0
                    
                    bar = create_progress_bar(progress)
                    print(f"Slot {i+1}: {bar} {progress:5.1f}% | {filename:<40} | {size:>8} | {format_time(elapsed)}")
                else:
                    print(f"Slot {i+1}: {'░' * 22:22} Waiting...{' ' * 60}")
            
            # Overall progress
            overall_progress = (self.completed / self.total_chapters) * 100
            elapsed = time.time() - self.start_time
            
            if self.completed > 0:
                avg_time_per_chapter = elapsed / self.completed
                remaining_chapters = self.total_chapters - self.completed
                eta = avg_time_per_chapter * remaining_chapters / self.max_workers
                eta_str = f" | ETA: {format_time(eta)}"
            else:
                eta_str = ""
            
            print(f"\nOverall: {self.completed}/{self.total_chapters} chapters ({overall_progress:.1f}%) | Elapsed: {format_time(elapsed)}{eta_str}")
            print("─" * 80)
    
    def start_chapter(self, chapter_num, filename, estimated_duration=60):
        """Start tracking a chapter"""
        with self.lock:
            # Find an empty slot
            for i, slot in enumerate(self.active_slots):
                if slot is None:
                    self.active_slots[i] = {
                        'chapter_num': chapter_num,
                        'filename': filename,
                        'start_time': time.time(),
                        'size': 0,
                        'estimated_duration': estimated_duration
                    }
                    break
    
    def update_chapter_size(self, chapter_num, size):
        """Update the size of an active chapter"""
        with self.lock:
            for slot in self.active_slots:
                if slot and slot['chapter_num'] == chapter_num:
                    slot['size'] = size
                    break
    
    def complete_chapter(self, chapter_num, success=True):
        """Mark a chapter as completed"""
        with self.lock:
            # Remove from active slots
            for i, slot in enumerate(self.active_slots):
                if slot and slot['chapter_num'] == chapter_num:
                    self.active_slots[i] = None
                    break
            
            if success:
                self.completed += 1
    
    def stop(self):
        """Stop the progress tracker"""
        self.stop_display.set()
        if self.display_thread:
            self.display_thread.join(timeout=1)

def monitor_file_size(output_path, chapter_num, tracker, stop_event):
    """Monitor file size during extraction"""
    while not stop_event.is_set():
        try:
            if output_path.exists():
                size = output_path.stat().st_size
                tracker.update_chapter_size(chapter_num, size)
        except:
            pass
        time.sleep(2)

def extract_single_chapter(args):
    """Extract a single chapter - designed to run in thread pool"""
    m4b_file, chapter, chapter_num, output_dir, tracker = args
    
    start_time_chapter = float(chapter['start_time'])
    end_time_chapter = float(chapter['end_time'])
    duration = end_time_chapter - start_time_chapter
    
    # Get title or use chapter number
    title = chapter.get('tags', {}).get('title', f'Chapter_{chapter_num:03d}')
    clean_title = clean_filename(title.strip())
    
    # Use original chapter title as filename (no sequential numbering)
    filename = f"{clean_title}.m4a"
    output_path = output_dir / filename
    
    # Start tracking
    tracker.start_chapter(chapter_num, filename, duration)
    
    # Start file monitoring
    stop_event = threading.Event()
    monitor_thread = threading.Thread(
        target=monitor_file_size,
        args=(output_path, chapter_num, tracker, stop_event)
    )
    monitor_thread.daemon = True
    monitor_thread.start()
    
    try:
        # Extract chapter
        cmd = [
            'ffmpeg', '-i', str(m4b_file),
            '-ss', str(start_time_chapter),
            '-to', str(end_time_chapter),
            '-c', 'copy',
            str(output_path),
            '-y', '-v', 'error'
        ]
        
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        stop_event.set()
        
        success = output_path.exists() and output_path.stat().st_size > 0
        tracker.complete_chapter(chapter_num, success)
        
        return chapter_num, success, None
        
    except Exception as e:
        stop_event.set()
        tracker.complete_chapter(chapter_num, False)
        return chapter_num, False, str(e)

def extract_chapters(m4b_file, max_workers=3):
    """Extract chapters from m4b file using multithreading"""
    print(f"Processing: {m4b_file}")
    print(f"Using {max_workers} concurrent extractions")
    print("=" * 80)
    
    # Create output directory
    base_name = m4b_file.stem
    output_dir = Path(base_name)
    output_dir.mkdir(exist_ok=True)
    
    # Get chapter information
    cmd = ['ffprobe', '-i', str(m4b_file), '-show_chapters', '-v', 'quiet', '-print_format', 'json']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"Error getting chapter info: {e}")
        return
    
    if 'chapters' not in data:
        print("No chapters found in file")
        return
    
    chapters = data['chapters']
    total_chapters = len(chapters)
    
    # Initialize progress tracker
    tracker = CleanProgressTracker(total_chapters, max_workers)
    
    # Give space for the progress display
    print('\n' * (max_workers + 4))
    
    # Prepare extraction tasks
    tasks = []
    for i, chapter in enumerate(chapters):
        tasks.append((m4b_file, chapter, i + 1, output_dir, tracker))
    
    # Extract chapters using thread pool
    successful_extractions = 0
    failed_extractions = []
    
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_chapter = {executor.submit(extract_single_chapter, task): task[2] for task in tasks}
            
            # Process completed tasks
            for future in as_completed(future_to_chapter):
                chapter_num = future_to_chapter[future]
                try:
                    chapter_num, success, error = future.result()
                    if success:
                        successful_extractions += 1
                    else:
                        failed_extractions.append((chapter_num, error))
                except Exception as e:
                    failed_extractions.append((chapter_num, str(e)))
    
    finally:
        tracker.stop()
    
    # Final summary
    print(f"\n\nExtraction Summary:")
    print(f"Successfully extracted: {successful_extractions}/{total_chapters} chapters")
    
    if failed_extractions:
        print(f"Failed extractions: {len(failed_extractions)}")
        for chapter_num, error in failed_extractions[:3]:  # Show first 3 errors
            print(f"  Chapter {chapter_num}: {error}")
        if len(failed_extractions) > 3:
            print(f"  ...and {len(failed_extractions) - 3} more")

def main():
    print("M4B Chapter Extractor (Clean Display)")
    print("=" * 50)
    
    # Get user preference for concurrency
    try:
        max_workers = int(input("Number of concurrent extractions (1-6, default 3): ") or "3")
        max_workers = max(1, min(6, max_workers))
    except ValueError:
        max_workers = 3
    
    # Find all .m4b files in current directory
    m4b_files = list(Path('.').glob('*.m4b'))
    
    if not m4b_files:
        print("No .m4b files found in current directory.")
        input("Press Enter to continue...")
        return
    
    print(f"Found {len(m4b_files)} .m4b file(s)")
    
    for file_index, m4b_file in enumerate(m4b_files):
        print(f"\nProcessing file {file_index + 1} of {len(m4b_files)}")
        extract_chapters(m4b_file, max_workers)
    
    print("\nAll files processed!")
    input("Press Enter to continue...")

if __name__ == "__main__":
    main()