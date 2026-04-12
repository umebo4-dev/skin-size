@echo off
python -c "from mitmproxy.tools.main import mitmdump; mitmdump()" -p 8080 -s "%~dp0collector.py"
