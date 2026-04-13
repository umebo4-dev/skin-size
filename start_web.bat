@if not "%~0"=="%~dp0.\%~nx0" start /min cmd /c,"%~dp0.\%~nx0" %* & goto :eof ::最小化で起動

@echo off
start http://localhost:5000
python "%~dp0app.py"