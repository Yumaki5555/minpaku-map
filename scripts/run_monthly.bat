@echo off
cd /d "%~dp0"
python update_sources.py >> "%~dp0..\state\last_run_log.txt" 2>&1
