#!/bin/bash

# Kill existing lightrag-server processes
PID=$(lsof -ti :8004 2>/dev/null)
if [ -n "$PID" ]; then
    echo "Killing existing process on port 8004 (PID: $PID)..."
    kill $PID 2>/dev/null
    sleep 1
    # Force kill if still alive
    kill -9 $PID 2>/dev/null 2>&1
fi

cd "$(dirname "$0")"

echo "Starting lightrag-server..."
exec lightrag-server
