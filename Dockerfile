# Python bot + Litestream (SQLite -> Cloudflare R2), same pattern as Charge Log.
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=litestream/litestream:0.3.13 /usr/local/bin/litestream /usr/local/bin/litestream
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fca_agent ./fca_agent
COPY run.py litestream.yml docker-entrypoint.sh ./
RUN chmod +x /app/docker-entrypoint.sh
ENV PORT=8080 DB_PATH=/app/data/agent.db BACKUP_DIR=/app/data/backups
EXPOSE 8080
ENTRYPOINT ["/app/docker-entrypoint.sh"]
