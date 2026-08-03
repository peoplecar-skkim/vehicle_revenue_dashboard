@echo off
chcp 65001 > nul
echo ========================================
echo  투루카 대시보드 데이터 업데이트
echo ========================================
echo.

cd /d "%~dp0"

python generate_data.py

echo.
echo GitHub에 변경사항 확인 중...
git add -A
git diff --cached --quiet
if %errorlevel% equ 0 (
    echo 변경사항 없음. Push 생략.
) else (
    git commit -m "data: %date% %time% 업데이트"
    git push
    echo GitHub 업로드 완료!
)

echo.
pause
