@echo off
rem duedillforge launcher (Windows): interpreter discovery -> __main__.py. Derived from memoforge (MIT, Grigorii Moskalev).
rem The plugin_data_dir chain mirrors scripts/ddforge/config.py.
setlocal EnableExtensions

set "DDF_SCRIPT_DIR=%~dp0"
for %%I in ("%DDF_SCRIPT_DIR%..") do set "DDF_PLUGIN_ROOT=%%~fI"
set "DDF_PROBE=import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)"

rem --- plugin_data_dir chain --------------------------------------
rem A candidate is taken only when it can be created AND written to - the same criterion as
rem config.plugin_data_dir(), otherwise the wrapper and the package would disagree.
set "DDF_DATA_DIR="
if defined CLAUDE_PLUGIN_DATA (
  call :mf_usable_dir "%CLAUDE_PLUGIN_DATA%"
  if not errorlevel 1 set "DDF_DATA_DIR=%CLAUDE_PLUGIN_DATA%"
)
set "DDF_HOME_CANDIDATE="
if defined LOCALAPPDATA set "DDF_HOME_CANDIDATE=%LOCALAPPDATA%\claude\plugin-data\ddforge"
if not defined LOCALAPPDATA if defined USERPROFILE set "DDF_HOME_CANDIDATE=%USERPROFILE%\.claude\plugin-data\ddforge"
if not defined DDF_DATA_DIR if defined DDF_HOME_CANDIDATE (
  call :mf_usable_dir "%DDF_HOME_CANDIDATE%"
  if not errorlevel 1 set "DDF_DATA_DIR=%DDF_HOME_CANDIDATE%"
)
if not defined DDF_DATA_DIR set "DDF_DATA_DIR=%DDF_PLUGIN_ROOT%\.data"
if not exist "%DDF_DATA_DIR%\" mkdir "%DDF_DATA_DIR%" >nul 2>&1

rem Service flag: print the resolved chain result and stop.
if /i "%~1"=="--print-plugin-data-dir" (
  echo %DDF_DATA_DIR%
  exit /b 0
)

rem --- discovery cache (<plugin_data_dir>\launcher.json) --------------------
rem Read first: a hit costs one interpreter start instead of up to three.
set "DDF_CACHE=%DDF_DATA_DIR%\launcher.json"
set "DDF_PYTHON="
set "DDF_PYTHON_ARGS="
set "DDF_FROM_CACHE="
set "DDF_CACHED="
if exist "%DDF_CACHE%" for /f "usebackq tokens=2 delims=[]" %%A in (`findstr /c:"python_cmd" "%DDF_CACHE%"`) do set "DDF_CACHED=%%A"
if defined DDF_CACHED set DDF_CACHED=%DDF_CACHED:"=%
if defined DDF_CACHED set "DDF_CACHED=%DDF_CACHED:, = %"
if defined DDF_CACHED for /f "tokens=1,2" %%A in ("%DDF_CACHED%") do (
  set "DDF_PYTHON=%%A"
  set "DDF_PYTHON_ARGS=%%B"
)
if defined DDF_PYTHON (
  "%DDF_PYTHON%" %DDF_PYTHON_ARGS% -c "%DDF_PROBE%" >nul 2>&1
  if not errorlevel 1 set "DDF_FROM_CACHE=1"
)
if not defined DDF_FROM_CACHE set "DDF_PYTHON="
if not defined DDF_FROM_CACHE set "DDF_PYTHON_ARGS="
if defined DDF_FROM_CACHE goto :found

rem --- interpreter discovery: python -> python3 -> py -3 --------------------
rem A Windows Store alias returns a non-zero exit code and is skipped.
python -c "%DDF_PROBE%" >nul 2>&1
if not errorlevel 1 (
  set "DDF_PYTHON=python"
  goto :discovered
)
python3 -c "%DDF_PROBE%" >nul 2>&1
if not errorlevel 1 (
  set "DDF_PYTHON=python3"
  goto :discovered
)
py -3 -c "%DDF_PROBE%" >nul 2>&1
if not errorlevel 1 (
  set "DDF_PYTHON=py"
  set "DDF_PYTHON_ARGS=-3"
  goto :discovered
)

echo ddf: no Python ^>= 3.9 found ^(tried python, python3, py -3^) 1>&2
exit /b 2

:discovered
rem A miss (or a stale cache) rewrites the cache with what discovery just found.
if "%DDF_PYTHON%"=="py" (
  >"%DDF_CACHE%.tmp" echo {"schema_version": 1, "kind": "launcher", "python_cmd": ["py", "-3"], "source": "ddf.cmd"}
) else (
  >"%DDF_CACHE%.tmp" echo {"schema_version": 1, "kind": "launcher", "python_cmd": ["%DDF_PYTHON%"], "source": "ddf.cmd"}
)
move /y "%DDF_CACHE%.tmp" "%DDF_CACHE%" >nul 2>&1

:found
rem --- PYTHONPATH += <plugin_data_dir>\site-packages -----------------------
if defined PYTHONPATH (
  set "PYTHONPATH=%DDF_DATA_DIR%\site-packages;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%DDF_DATA_DIR%\site-packages"
)

"%DDF_PYTHON%" %DDF_PYTHON_ARGS% "%DDF_PLUGIN_ROOT%\scripts\ddforge\__main__.py" %*
exit /b %ERRORLEVEL%

:mf_usable_dir
rem Create the directory and prove it is writable; exit code 0 = usable.
set "DDF_TRY=%~1"
if not defined DDF_TRY exit /b 1
if not exist "%DDF_TRY%\" mkdir "%DDF_TRY%" >nul 2>&1
if not exist "%DDF_TRY%\" exit /b 1
(echo ok)>"%DDF_TRY%\.ddf-write-probe" 2>nul
if not exist "%DDF_TRY%\.ddf-write-probe" exit /b 1
del /q "%DDF_TRY%\.ddf-write-probe" >nul 2>&1
exit /b 0
