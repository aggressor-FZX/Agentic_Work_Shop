#!/usr/bin/env bash
set -euo pipefail
cd /app
# Optional: pre-download embeddings to speed cold start (comment if heavy)
# python -c "import sentence_transformers; sentence_transformers.SentenceTransformer('all-MiniLM-L6-v2')"
exec celery -A celery_app.celery_app worker --loglevel=INFO --concurrency=${CONCURRENCY:-2} --hostname=agentic@%h
