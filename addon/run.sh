#!/bin/sh
set -eu

mkdir -p /data/photos
cd /app
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
