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

powershell.exe -NoProfile -Command "Set-Clipboard -Value $env:SOMTODAY_HELPER_DIR" >nul 2>&1
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
set "BROWSER_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%BROWSER_EXE%" set "BROWSER_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%BROWSER_EXE%" set "BROWSER_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if exist "%BROWSER_EXE%" (
  start "" "%BROWSER_EXE%" "chrome://extensions/"
) else (
  start "" "chrome://extensions/"
)
set "BROWSER_NAME=Chrome"
goto instructions

:edge
set "BROWSER_EXE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%BROWSER_EXE%" set "BROWSER_EXE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%BROWSER_EXE%" set "BROWSER_EXE=%LocalAppData%\Microsoft\Edge\Application\msedge.exe"
if exist "%BROWSER_EXE%" (
  start "" "%BROWSER_EXE%" "edge://extensions/"
) else (
  start "" "edge://extensions/"
)
set "BROWSER_NAME=Edge"

:instructions
echo.
echo De extensiepagina van %BROWSER_NAME% en deze map zijn geopend.
echo Het pad naar deze map staat ook op het klembord.
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

