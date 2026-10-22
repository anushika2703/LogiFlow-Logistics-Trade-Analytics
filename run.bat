@echo off
setlocal
cd /d "%~dp0"
echo ==========================================
echo LogiFlow - Logistics & Trade Analytics Demo
echo ==========================================
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_pipeline.py
if errorlevel 1 (
  echo Pipeline failed. Check the error above.
  pause
  exit /b 1
)
echo.
echo Starting dashboard in your browser...
streamlit run dashboard.py
pause
