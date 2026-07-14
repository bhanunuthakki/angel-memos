@echo off
rem Double-click launcher for the Angel Memos GUI.
rem Uses the Windows "py" launcher; falls back to python on PATH.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 "angel_memos_gui.py" & goto :eof)
python "angel_memos_gui.py"
