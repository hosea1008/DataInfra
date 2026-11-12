#!/usr/bin/env bash
# start_visualize.sh - (re)start the visualize FastAPI service (uvicorn, port 8080).
# Idempotent: stops a previously started instance (tracked via pid file) first.
# NOTE: VISUALIZE_DBINFO (if exported by the caller) is inherited by the
# nohup'd process automatically; this script never sets or unsets it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOG_DIR="$ROOT/visualize/log"
LOG_FILE="$LOG_DIR/visualize.log"
PID_FILE="$LOG_DIR/visualize.pid"

mkdir -p "$LOG_DIR"

# --- Idempotent restart: stop a previous instance if we own it -------------
if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
        CMDLINE="$(tr '\0' ' ' < "/proc/$OLD_PID/cmdline" 2>/dev/null || true)"
        if [[ "$CMDLINE" == *uvicorn* && "$CMDLINE" == *app.main* ]]; then
            echo "stopping existing visualize process (pid $OLD_PID)..."
            kill -TERM "$OLD_PID" 2>/dev/null || true
            for _ in $(seq 1 10); do
                kill -0 "$OLD_PID" 2>/dev/null || break
                sleep 1
            done
            if kill -0 "$OLD_PID" 2>/dev/null; then
                echo "pid $OLD_PID did not exit after TERM, sending KILL" >&2
                kill -KILL "$OLD_PID" 2>/dev/null || true
                sleep 1
            fi
        else
            echo "WARNING: pid $OLD_PID in $PID_FILE is alive but does not look" >&2
            echo "WARNING: like a visualize (uvicorn app.main) process; NOT killing it." >&2
        fi
    fi
fi

# --- Start -----------------------------------------------------------------
# Launch from $ROOT so uvicorn's --app-dir relative layout is unambiguous.
cd "$ROOT"
nohup "$ROOT/visualize/.venv/bin/python" -m uvicorn app.main:app --app-dir "$ROOT/visualize" --host 0.0.0.0 --port 8080 >> "$LOG_FILE" 2>&1 &
PID="$!"
echo "$PID" > "$PID_FILE"

# --- Health wait ------------------------------------------------------------
for _ in $(seq 1 20); do
    if curl -sf http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
        echo "visualize service started (pid $PID, port 8080)"
        exit 0
    fi
    sleep 1
done

echo "visualize service failed to become healthy within 20s (pid $PID); last 30 log lines:" >&2
tail -n 30 "$LOG_FILE" >&2 || true
exit 1
