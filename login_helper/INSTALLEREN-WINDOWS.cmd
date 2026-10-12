@echo off
setlocal
title Somtoday Login Helper installeren

set "SOMTODAY_HELPER_DIR=%~dp0"

if not exist "%SOMTODAY_HELPER_DIR%manifest.json" (
  echo.
  echo Dit bestand kan niet rechtstreeks vanuit het ZIP-bestand worden gestart.
  echo Klik met rechts op de download, kies Alles uitpakken en start dit bestand
  echo daarna opnieuw vanuit de uitgepakte map.
  echo.
  pause
  exit /b 1
)

start "" explorer.exe "%SOMTODAY_HELPER_DIR%"

echo.
echo Somtoday Login Helper
echo ======================
echo.
echo Welke browser gebruik je?
echo   C = Google Chrome
echo   E = Microsoft Edge
echo.
choice /C CE /N /M "Kies C of E: "
if errorlevel 2 goto edge

:chrome
set "EXTENSIONS_URL=chrome://extensions/"
set "BROWSER_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%BROWSER_EXE%" set "BROWSER_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%BROWSER_EXE%" set "BROWSER_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"
set "BROWSER_NAME=Chrome"
goto open_browser

:edge
set "EXTENSIONS_URL=edge://extensions/"
set "BROWSER_EXE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%BROWSER_EXE%" set "BROWSER_EXE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%BROWSER_EXE%" set "BROWSER_EXE=%LocalAppData%\Microsoft\Edge\Application\msedge.exe"
set "BROWSER_NAME=Edge"

:open_browser
if not exist "%BROWSER_EXE%" goto browser_not_found
start "" "%BROWSER_EXE%" --new-window "%EXTENSIONS_URL%"
set "PAGE_STATUS=De extensiepagina van %BROWSER_NAME% wordt in een nieuw venster geopend."
goto instructions

:browser_not_found
powershell.exe -NoProfile -Command "Set-Clipboard -Value $env:EXTENSIONS_URL" >nul 2>&1
echo.
echo %BROWSER_NAME% kon niet automatisch worden gevonden.
echo Open %BROWSER_NAME% zelf en plak dit adres in de adresbalk:
echo.
echo   %EXTENSIONS_URL%
echo.
echo Het adres staat ook op het klembord.
set "PAGE_STATUS=De map is geopend; open de extensiepagina met het adres hierboven."

:instructions
echo.
echo %PAGE_STATUS%
echo Verschijnt de extensiepagina niet? Open %BROWSER_NAME% en ga naar:
echo   %EXTENSIONS_URL%
echo.
echo Doe nu alleen nog dit:
echo   1. Zet Ontwikkelaarsmodus aan.
echo   2. Klik op Uitgepakte extensie laden.
echo   3. Kies de map die zojuist is geopend en klik op Map selecteren.
echo   4. Zet Somtoday Login Helper vast via het puzzelstukje in de adresbalk.
echo.
echo Daarna is de helper klaar voor gebruik. Zie LEESMIJ-EERST.txt voor de
echo korte gebruiksstappen.
echo.
pause

