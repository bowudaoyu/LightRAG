#!/bin/bash

# Configuration
LIGHTRAG_BIN=".venv/bin/lightrag-server"
PORT=8003
LOG_FILE="lightrag.log"

# Check if the binary exists
if [ ! -f "$LIGHTRAG_BIN" ]; then
    echo "Error: LightRAG binary not found at $LIGHTRAG_BIN (in $SCRIPT_DIR)"
    echo "Please check the path or install the application first."
    exit 1
fi

# Find running process
PID=$(ps -ef | grep "$LIGHTRAG_BIN" | grep -v grep | awk '{print $2}')

if [ -n "$PID" ]; then
    echo "Stopping existing LightRAG server (PID: $PID)..."
    kill $PID
    sleep 2
    
    # Force kill if still running
    if ps -p $PID > /dev/null; then
        echo "Force killing..."
        kill -9 $PID
    fi
    echo "Stopped."
fi

echo "Starting LightRAG server on port $PORT..."
# Using nohup to run in background
nohup $LIGHTRAG_BIN --port $PORT > $LOG_FILE 2>&1 &

NEW_PID=$!
echo "LightRAG server started with PID $NEW_PID"
echo "Logs are being written to $LOG_FILE"
