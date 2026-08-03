@echo off
REM Runs every cm_ vanilla-derived generator in sequence. Pass-through args
REM (such as --game-dir "PATH") reach each generator. Excludes the GlorpUI sync,
REM the GUI update, translation, and upload, which are run separately.
setlocal
cd /d "%~dp0.."

echo [1/3] Auto town rights eligibility triggers...
python tools\cm_generate_town_rights_allow.py %* || goto :fail

echo [2/3] Best Urban Right map mode...
python tools\cm_generate_town_rights_map_mode.py %* || goto :fail

echo [3/3] Placement finder values...
python tools\cm_generate_proximity_finder.py %* || goto :fail

echo All generators finished.
exit /b 0

:fail
echo Generator failed with exit code %errorlevel%.
exit /b 1
