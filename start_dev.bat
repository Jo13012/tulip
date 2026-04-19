@echo off
setlocal

REM ── polymarket_rust_service ──────────────────────────────────────────────────
echo Starting Rust service...
if not exist "polymarket_rust_service\.env" (
    copy "polymarket_rust_service\.env.example" "polymarket_rust_service\.env"
    echo Created polymarket_rust_service\.env from example
)
start "Rust Service" cmd /k "cd polymarket_rust_service && %USERPROFILE%\.cargo\bin\cargo.exe run"

REM ── Python backend ───────────────────────────────────────────────────────────
echo Starting Python backend...
if not exist "backend\.env" (
    copy "backend\.env.example" "backend\.env"
    echo Created backend\.env from example - fill in secrets before live trading
)
start "Python Backend" cmd /k "cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload"

REM ── Frontend ─────────────────────────────────────────────────────────────────
echo Starting frontend...
start "Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo.
echo Services starting [LIVE MODE]:
echo   Rust service:    http://127.0.0.1:8020
echo   Python backend:  http://127.0.0.1:8010
echo   Frontend:        http://localhost:3010
echo   API docs:        http://127.0.0.1:8010/docs
