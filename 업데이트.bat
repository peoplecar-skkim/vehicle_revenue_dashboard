@echo off
chcp 65001 > nul
echo ========================================
echo  투루카 대시보드 데이터 업데이트
echo ========================================
echo.

cd /d "%~dp0"

python generate_data.py

