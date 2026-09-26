@echo off
setlocal
python -m pip install -r requirements.txt
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
python -m PyInstaller --clean --onefile --windowed --name NSE_Bullish_Scanner app.py
python -m PyInstaller --clean --onefile --windowed --name NSE_Bullish_Scanner_Updater updater.py
echo.
echo ========================================
echo BUILD COMPLETE
echo dist\NSE_Bullish_Scanner.exe
echo dist\NSE_Bullish_Scanner_Updater.exe
echo ========================================
pause
