import os
import re
from pathlib import Path

def extract_numbers(filename):
    """Extract part, chapter, subchapter numbers from filename"""
    # Pattern: Nabokov_Jeremy Irons - Lolita XX - YY - ZZ.mp3
    pattern = r'Lolita\s+(\d+)\s+-\s+(\d+)\s+-\s+(\d+)\.mp3'
    match = re.search(pattern, filename)
    if match:
        part = int(match.group(1))
        chapter = int(match.group(2))
        subchapter = int(match.group(3))
        return (part, chapter, subchapter)
    return None

def preview_renaming(directory='.'):
    """Preview what the renaming will look like without actually doing it"""
    print("=== PREVIEW MODE ===")
    print("This shows what files will be renamed to:\n")
    
    # Get all mp3 files
    mp3_files = []
    for file in os.listdir(directory):
        if file.endswith('.mp3') and 'Lolita' in file:
            numbers = extract_numbers(file)
            if numbers:
                mp3_files.append((file, numbers))
    
    if not mp3_files:
        print("No matching Lolita audiobook files found!")
        return False
    
    # Sort by part, chapter, subchapter
    mp3_files.sort(key=lambda x: x[1])
    
    # Show preview
    for i, (old_filename, numbers) in enumerate(mp3_files, 1):
        new_filename = f"Lolita - {i:03d}.mp3"
        print(f"{i:3d}. {old_filename}")
        print(f"     -> {new_filename}")
        print(f"     (Part {numbers[0]}, Ch {numbers[1]}, Sub {numbers[2]})\n")
    
    print(f"Total files to rename: {len(mp3_files)}")
    return True

def rename_audiobook_files(directory='.', dry_run=True):
    """Rename audiobook files to sequential numbering"""
    
    if dry_run:
        return preview_renaming(directory)
    
    print("=== ACTUAL RENAMING ===")
    
    # Get all mp3 files
    mp3_files = []
    for file in os.listdir(directory):
        if file.endswith('.mp3') and 'Lolita' in file:
            numbers = extract_numbers(file)
            if numbers:
                mp3_files.append((file, numbers))
    
    if not mp3_files:
        print("No matching Lolita audiobook files found!")
        return
    
    # Sort by part, chapter, subchapter
    mp3_files.sort(key=lambda x: x[1])
    
    # Rename files sequentially
    for i, (old_filename, _) in enumerate(mp3_files, 1):
        new_filename = f"Lolita - {i:03d}.mp3"
        old_path = os.path.join(directory, old_filename)
        new_path = os.path.join(directory, new_filename)
        
        try:
            print(f"Renaming: {old_filename}")
            print(f"      -> {new_filename}")
            os.rename(old_path, new_path)
        except Exception as e:
            print(f"Error renaming {old_filename}: {e}")
            return
    
    print(f"\nSuccessfully renamed {len(mp3_files)} files!")

def main():
    print("Audiobook File Renamer for Lolita")
    print("=" * 40)
    
    # Get directory
    directory = input("Enter directory path (or press Enter for current directory): ").strip()
    if not directory:
        directory = '.'
    
    if not os.path.exists(directory):
        print(f"Directory {directory} does not exist!")
        return
    
    print(f"\nProcessing files in: {os.path.abspath(directory)}\n")
    
    # First show preview
    if not rename_audiobook_files(directory, dry_run=True):
        return
    
    # Ask for confirmation
    print("\n" + "=" * 50)
    confirm = input("Do you want to proceed with the renaming? (y/N): ").strip().lower()
    
    if confirm in ['y', 'yes']:
        print()
        rename_audiobook_files(directory, dry_run=False)
    else:
        print("Renaming cancelled.")

if __name__ == "__main__":
    main()