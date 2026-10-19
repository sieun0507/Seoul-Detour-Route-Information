@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 실시간 우회 길찾기를 실행합니다. 브라우저가 열리지 않으면 http://localhost:8501 로 접속하세요.
echo 종료하려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으세요.
.venv\Scripts\streamlit run app.py
pause
