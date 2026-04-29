#!/bin/bash
#
# Usage: ./start.sh [dev|prod]
#
# Switches between .env.dev and .env.prod by copying the chosen file over
# .env (lightrag-server reads .env via python-dotenv with override=False,
# so the file is the source of truth unless OS env wins).

cd "$(dirname "$0")"

APP_ENV="${1:-dev}"
case "$APP_ENV" in
    dev|prod) ;;
    *)
        echo "ERROR: Unknown environment '$APP_ENV'. Usage: $0 [dev|prod]"
        exit 1
        ;;
esac

ENV_FILE=".env.${APP_ENV}"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found. Create it from .env.example or copy from .env."
    exit 1
fi

# Copy env-specific file over .env so dotenv picks it up.
cp -f "$ENV_FILE" .env
echo "Using environment: $APP_ENV ($ENV_FILE → .env)"
DB_NAME=$(grep -E '^POSTGRES_DATABASE=' .env | head -1 | cut -d= -f2)
echo "  POSTGRES_DATABASE=$DB_NAME"

# Kill only the LISTENING process on port 8004 (not transient client conns
# from other apps in CLOSE_WAIT / TIME_WAIT state).
PID=$(lsof -ti :8004 -sTCP:LISTEN 2>/dev/null | head -1)
if [ -n "$PID" ]; then
    echo "Killing existing lightrag-server (PID: $PID)..."
    kill "$PID" 2>/dev/null || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
fi

LOG_DIR="$(pwd)/logs"
mkdir -p "$LOG_DIR"

echo "Starting lightrag-server in background..."
nohup lightrag-server > "$LOG_DIR/lightrag-server.log" 2>&1 &
echo "lightrag-server started (PID: $!), log: $LOG_DIR/lightrag-server.log"
echo "tail -f $LOG_DIR/lightrag-server.log"
