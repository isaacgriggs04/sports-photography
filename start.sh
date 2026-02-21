#!/bin/bash
# Start both backend and frontend servers for SportsPic webapp

cd "$(dirname "$0")"

# Kill any existing processes on ports 8080 and 5173
echo "Stopping any existing servers..."
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
lsof -ti:5173 | xargs kill -9 2>/dev/null || true
sleep 2

# Start Flask backend in background
echo "Starting backend (Flask on http://localhost:8080)..."
python3 app.py &
BACKEND_PID=$!
sleep 2

# Start Vite frontend
echo "Starting frontend (Vite on http://localhost:5173)..."
echo ""
echo "Webapp ready at: http://localhost:5173"
echo "Press Ctrl+C to stop both servers."
echo ""

cd frontend
npm run dev

# When frontend stops, kill backend too
kill $BACKEND_PID 2>/dev/null || true
