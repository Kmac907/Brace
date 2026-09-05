@echo off
pwsh -NoProfile -NonInteractive -File "%~dp0..\fake-impl\az.ps1" %*
exit /b %ERRORLEVEL%
