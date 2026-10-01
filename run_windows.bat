@echo off
cd /d "%~dp0"
echo AI 정보카드를 준비합니다...
where python > nul 2>&1
if errorlevel 1 (
  echo Python이 없습니다. https://www.python.org/downloads/ 에서 설치할 때 "Add python.exe to PATH"를 체크하세요.
  pause
  exit /b 1
)
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if not exist .env (
  copy .env.example .env > nul
  echo.
  echo .env 파일을 만들었습니다. 메모장으로 열어 GEMINI_API_KEY= 뒤에 키를 붙여 넣고 저장한 뒤 이 파일을 다시 실행하세요.
  notepad .env
  pause
  exit /b 0
)
python seed.py
echo.
echo 브라우저에서 http://127.0.0.1:8000 을 여세요. 끝내려면 이 창을 닫으세요.
start "" http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
