#!/bin/sh
set -e
DB=/app/data/agent.db
mkdir -p /app/data

if [ -n "$LITESTREAM_BUCKET" ]; then
  echo "[entrypoint] durable mode: Cloudflare R2 configured"
  if [ ! -f "$DB" ]; then
    litestream restore -if-replica-exists -config /app/litestream.yml "$DB" || true
    if [ -f "$DB" ]; then echo "[entrypoint] restored database from R2"; else echo "[entrypoint] no replica yet, starting empty"; fi
  fi
  exec litestream replicate -exec "python run.py" -config /app/litestream.yml
else
  echo "[entrypoint] ephemeral mode: no R2 settings, tablets and releases are lost when the server restarts"
  exec python run.py
fi
