@echo off
setlocal enabledelayedexpansion

echo Combining The Mongoliad audiobooks...
echo.

:: Create temporary file list for ffmpeg concat
set "filelist=mongoliad_filelist.txt"

:: Write the file list
echo file 'Neal Stephenson - The Mongoliad_ Book 1.m4b' > "%filelist%"
echo file 'Neal Stephenson - The Mongoliad_ Book 2.m4b' >> "%filelist%"
echo file 'Neal Stephenson - The Mongoliad_ Book 3.m4b' >> "%filelist%"

:: Check if all source files exist
if not exist "Neal Stephenson - The Mongoliad_ Book 1.m4b" (
    echo ERROR: Book 1 not found!
    goto :cleanup
)
if not exist "Neal Stephenson - The Mongoliad_ Book 2.m4b" (
    echo ERROR: Book 2 not found!
    goto :cleanup
)
if not exist "Neal Stephenson - The Mongoliad_ Book 3.m4b" (
    echo ERROR: Book 3 not found!
    goto :cleanup
)

echo All source files found. Starting ffmpeg...
echo.

:: Run ffmpeg to combine the files (audio only, ignore cover art)
ffmpeg -f concat -safe 0 -i "%filelist%" -map 0:a -c copy "Neal Stephenson - The Mongoliad.m4b"

:: Check if ffmpeg succeeded
if %ERRORLEVEL% equ 0 (
    echo.
    echo SUCCESS: Combined audiobook created!
    echo Output: Neal Stephenson - The Mongoliad.m4b
    echo.
    echo Would you like to delete the original 3 separate files? (y/n)
    set /p "delete_originals="
    if /i "!delete_originals!"=="y" (
        del "Neal Stephenson - The Mongoliad_ Book 1.m4b"
        del "Neal Stephenson - The Mongoliad_ Book 2.m4b"
        del "Neal Stephenson - The Mongoliad_ Book 3.m4b"
        echo Original files deleted.
    )
) else (
    echo.
    echo ERROR: ffmpeg failed to combine the files.
    echo Check that the files aren't corrupted and ffmpeg is working properly.
)

:cleanup
:: Clean up temporary file list
if exist "%filelist%" del "%filelist%"

echo.
pause