param([string]$InstallRoot = "C:\Bectanse\MT5Worker", [string]$TerminalPath)
& "$InstallRoot\venv\Scripts\python.exe" "$InstallRoot\tools\test_real_mt5.py" --terminal-path $TerminalPath
