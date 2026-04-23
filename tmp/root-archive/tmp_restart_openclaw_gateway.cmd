@echo off
timeout /t 2 /nobreak >nul
taskkill /PID 6568 /F >nul 2>nul
taskkill /PID 2376 /F >nul 2>nul
timeout /t 2 /nobreak >nul
call "C:\Users\Administrator\.openclaw\gateway.cmd"
