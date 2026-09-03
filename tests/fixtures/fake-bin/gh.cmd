@echo off
pwsh -NoProfile -NonInteractive -File "%~dp0..\fake-impl\gh.ps1" %*
exit /b %ERRORLEVEL%
