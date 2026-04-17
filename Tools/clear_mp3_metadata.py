import os
from mutagen.mp3 import MP3
from mutagen.id3 import ID3

def clear_mp3_metadata(directory):
    """
    Clear all metadata from MP3 files in the specified directory.
    """
    print("="*60)
    print("MP3 METADATA CLEANER")
    print("="*60)
    print(f"\nScanning folder: {directory}\n")
    
    if not os.path.exists(directory):
        print(f"ERROR: Folder does not exist!")
        print(f"Please check the path: {directory}")
        input("\nPress Enter to exit...")
        return
    
    mp3_files = [f for f in os.listdir(directory) if f.endswith('.mp3')]
    
    if not mp3_files:
        print("No MP3 files found in the directory.")
        input("\nPress Enter to exit...")
        return
    
    print(f"Found {len(mp3_files)} MP3 files.")
    print(f"Starting to clear metadata...\n")
    print("-"*60)
    
    success_count = 0
    error_count = 0
    
    for index, filename in enumerate(mp3_files, 1):
        filepath = os.path.join(directory, filename)
        try:
            # Load the MP3 file
            audio = MP3(filepath, ID3=ID3)
            
            # Delete all tags
            audio.delete()
            
            # Save the file without tags
            audio.save()
            
            print(f"[{index}/{len(mp3_files)}] ✓ Cleared: {filename}")
            success_count += 1
            
        except Exception as e:
            print(f"[{index}/{len(mp3_files)}] ✗ Error: {filename}")
            print(f"    Reason: {str(e)}")
            error_count += 1
    
    print("-"*60)
    print(f"\nSUMMARY:")
    print(f"  Successfully cleared: {success_count} files")
    if error_count > 0:
        print(f"  Errors: {error_count} files")
    print(f"\nAll done!")
    print("="*60)
    
    input("\nPress Enter to exit...")

if __name__ == "__main__":
    # Use the current directory where this script is located
    folder_path = os.path.dirname(os.path.abspath(__file__))
    
    try:
        clear_mp3_metadata(folder_path)
    except Exception as e:
        print(f"\nUNEXPECTED ERROR: {str(e)}")
        input("\nPress Enter to exit...")