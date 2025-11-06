#!/usr/bin/env python3
# enqueue.py
import os, sys, json
from celery_app import celery_app

def usage():
    print("Usage: python enqueue.py \"<goal>\" [scope1 scope2 ...] [--iters N]")
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        usage()
    goal = sys.argv[1]
    rest = sys.argv[2:]
    iters = 3
    if "--iters" in rest:
        i = rest.index("--iters")
        iters = int(rest[i+1]); rest = rest[:i] + rest[i+2:]
    scope = rest or ["src","tests"]

    # Send to Celery/RabbitMQ
    async_res = celery_app.send_task(
        "tasks.run_manager_task",
        args=[goal, scope, iters],
        queue="agentic.work"
    )
    print("Queued:", async_res.id)
    # optional: wait for result
    # print(async_res.get(timeout=3600))

if __name__ == "__main__":
    main()
