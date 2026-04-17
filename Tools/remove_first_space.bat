@echo off
setlocal enabledelayedexpansion

echo Starting filename cleanup...
echo.

for %%f in (*.*) do (
    set "filename=%%f"
    
    REM Skip the batch file itself
    if /i not "!filename!"=="%~nx0" (
        REM Find the first space and extract everything after it
        for /f "tokens=1* delims= " %%a in ("!filename!") do (
            set "newname=%%b"
        )
        
        REM Only rename if there was text after the first space
        if defined newname (
            if not "!newname!"=="" (
                echo Renaming: "!filename!" to "!newname!"
                ren "!filename!" "!newname!"
            ) else (
                echo Skipping: "!filename!" - no text after first space
            )
        ) else (
            echo Skipping: "!filename!" - no space found
        )
    )
)

echo.
echo Cleanup complete!
pause