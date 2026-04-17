#!/usr/bin/env python3
"""
MP3 Repair Script
Re-encodes MP3 files using ffmpeg to fix audio errors and corruption.
"""

import os
import subprocess
import sys
from pathlib import Path
import shutil

def check_ffmpeg():
    """Check if ffmpeg is installed and accessible."""
    try:
        subprocess.run(['ffmpeg', '-version'], 
                      stdout=subprocess.PIPE, 
                      stderr=subprocess.PIPE, 
                      check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def repair_mp3(input_file, output_file, backup=True):
    """
    Repair an MP3 file by re-encoding it with ffmpeg.
    
    Args:
        input_file: Path to the input MP3 file
        output_file: Path to the output MP3 file
        backup: Whether to create a backup of the original file
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Create backup if requested
        if backup:
            backup_file = str(input_file) + '.backup'
            shutil.copy2(input_file, backup_file)
            print(f"  ✓ Backup created: {backup_file}", flush=True)
        
        print(f"  ⏳ Re-encoding (this may take a minute)...", flush=True)
        
        # ffmpeg command to re-encode the MP3
        # -i: input file
        # -acodec libmp3lame: use MP3 codec
        # -b:a 192k: bitrate (adjust as needed)
        # -ar 44100: sample rate
        # -y: overwrite output file
        # -loglevel error: only show errors, not progress (cleaner output)
        cmd = [
            'ffmpeg',
            '-i', str(input_file),
            '-acodec', 'libmp3lame',
            '-b:a', '192k',
            '-ar', '44100',
            '-loglevel', 'error',
            '-y',
            str(output_file)
        ]
        
        # Run ffmpeg - let it output directly to console
        result = subprocess.run(cmd)
        
        if result.returncode == 0:
            print(f"  ✓ Successfully repaired: {input_file.name}", flush=True)
            return True
        else:
            print(f"  ✗ Error repairing {input_file.name}", flush=True)
            return False
            
    except Exception as e:
        print(f"  ✗ Exception while repairing {input_file.name}: {str(e)}", flush=True)
        return False

def main():
    """Main function to repair all MP3 files in the current directory."""
    
    # Check if ffmpeg is available
    if not check_ffmpeg():
        print("ERROR: ffmpeg is not installed or not in PATH", flush=True)
        print("Please install ffmpeg first:", flush=True)
        print("  - Windows: Download from https://ffmpeg.org/download.html", flush=True)
        print("  - Mac: brew install ffmpeg", flush=True)
        print("  - Linux: sudo apt-get install ffmpeg", flush=True)
        sys.exit(1)
    
    # Get current directory
    current_dir = Path.cwd()
    print(f"Scanning directory: {current_dir}\n", flush=True)
    
    # Find all MP3 files
    mp3_files = list(current_dir.glob('*.mp3'))
    
    # Exclude backup files
    mp3_files = [f for f in mp3_files if not f.name.endswith('.backup')]
    
    if not mp3_files:
        print("No MP3 files found in the current directory.", flush=True)
        sys.exit(0)
    
    print(f"Found {len(mp3_files)} MP3 file(s) to repair.\n", flush=True)
    
    # Process each file
    success_count = 0
    for i, mp3_file in enumerate(mp3_files, 1):
        print(f"[{i}/{len(mp3_files)}] Processing: {mp3_file.name}", flush=True)
        
        # Create output filename (temporary)
        temp_output = mp3_file.parent / f"{mp3_file.stem}_repaired.mp3"
        
        # Repair the file
        if repair_mp3(mp3_file, temp_output, backup=True):
            # Replace original with repaired version
            shutil.move(str(temp_output), str(mp3_file))
            success_count += 1
        else:
            # Clean up temp file if it exists
            if temp_output.exists():
                temp_output.unlink()
        
        print(flush=True)
    
    # Summary
    print("=" * 50, flush=True)
    print(f"Repair complete!", flush=True)
    print(f"Successfully repaired: {success_count}/{len(mp3_files)} files", flush=True)
    print(f"Backups saved with .backup extension", flush=True)
    print("=" * 50, flush=True)

if __name__ == "__main__":
    main()