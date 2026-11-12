#!/usr/bin/env bash
# stop_visualize.sh - stop the visualize FastAPI service tracked via its pid file.
# Idempotent: safe to run when the service is already stopped or never started.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PID_FILE="$ROOT/visualize/log/visualize.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "not running (no pid file)"
    exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"

if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    CMDLINE="$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null || true)"
    if [[ "$CMDLINE" == *uvicorn* && "$CMDLINE" == *app.main* ]]; then
        echo "stopping visualize (pid $PID)..."
        kill -TERM "$PID" 2>/dev/null || true
        for _ in $(seq 1 10); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$PID" 2>/dev/null; then
            echo "pid $PID did not exit after TERM, sending KILL" >&2
            kill -KILL "$PID" 2>/dev/null || true
        fi
        echo "stopped (pid $PID)"
    else
        echo "WARNING: pid $PID is alive but does not look like a visualize" >&2
        echo "WARNING: (uvicorn app.main) process; NOT killing it. Removing stale pid file." >&2
    fi
else
    echo "process $PID not running, cleaning up"
fi

rm -f "$PID_FILE"
