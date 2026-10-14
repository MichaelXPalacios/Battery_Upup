#!/usr/bin/env bash
# Start the platform's own Redis on :6390. The shared :6379 instance belongs to another application and is full.
# Append-only persistence keeps the halt flag across Redis restarts. Data lives under DISPATCH_HOME/runtime/redis.
# Stop it with: "/c/Program Files/Redis/redis-cli" -p 6390 SHUTDOWN
set -euo pipefail
HOME_DIR="${DISPATCH_HOME:-C:/Users/Javier/Repo/BaseP/research-data/dispatch}"
mkdir -p "$HOME_DIR/runtime/redis"
exec "/c/Program Files/Redis/redis-server.exe" --port 6390 --bind 127.0.0.1 --dir "$HOME_DIR/runtime/redis" \
  --appendonly yes --appendfsync everysec --save "" --maxmemory 256mb --maxmemory-policy noeviction
