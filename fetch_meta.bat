@echo off
python -m pip install cloudscraper -q
python "%~dp0fetch_meta.py"
pause
