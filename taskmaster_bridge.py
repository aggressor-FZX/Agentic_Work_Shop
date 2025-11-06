# taskmaster_bridge.py
from __future__ import annotations
import json, os, time, subprocess, threading, queue, uuid
from typing import Dict, Any, List, Optional

# ---- Replace these with your real TaskMaster endpoints later ----
QUEUE_FILE = ".taskmaster/queue.json"   # [{id, goal, scope, iterations}]
STATUS_FILE = ".taskmaster/status.json" # {id: {state}}

MAX_PARALLEL = int(os.getenv("TM_MAX_PARALLEL", "3"))

def load_queue() -> List[Dict[str, Any]]:
    if not os.path.exists(QUEUE_FILE): return []
    return json.loads(open(QUEUE_FILE).read() or "[]")

def save_status(status: Dict[str, Any]):
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)

def worker(task: Dict[str, Any], status: Dict[str, Any]):
    tid = task["id"]
    goal = task["goal"]
    scope = task.get("scope", ["src","tests"])
    iters = int(task.get("iterations", 3))
    status[tid] = {"state":"RUNNING","goal":goal,"scope":scope}
    save_status(status)
    try:
        # launch a separate process for isolation
        proc = subprocess.run(
            [os.environ.get("PYBIN","python"), "Orchistrate.py", goal, *scope, str(iters)],
            capture_output=True, text=True
        )
        status[tid] = {
            "state": "DONE" if proc.returncode==0 else "ERROR",
            "rc": proc.returncode,
            "stdout_tail": proc.stdout[-1200:],
            "stderr_tail": proc.stderr[-1200:]
        }
    except Exception as e:
        status[tid] = {"state":"ERROR","error":str(e)}
    finally:
        save_status(status)

def main_loop():
    status: Dict[str, Any] = {}
    active: Dict[str, threading.Thread] = {}
    while True:
        q = load_queue()
        # start new workers if capacity
        for t in q:
            tid = t["id"]
            if tid in active: continue
            # skip completed
            if status.get(tid,{}).get("state") in {"DONE","ERROR"}: continue
            if len([th for th in active.values() if th.is_alive()]) >= MAX_PARALLEL:
                break
            th = threading.Thread(target=worker, args=(t,status), daemon=True)
            active[tid] = th
            th.start()

        # cleanup finished
        for tid, th in list(active.items()):
            if not th.is_alive():
                del active[tid]
        time.sleep(2)

if __name__ == "__main__":
    # seed an example queue if empty
    if not os.path.exists(QUEUE_FILE):
        open(QUEUE_FILE,"w").write(json.dumps([{
            "id": str(uuid.uuid4()),
            "goal": "Refactor src/foo for DI; keep tests green.",
            "scope": ["src/foo","tests/foo"],
            "iterations": 3
        }], indent=2))
    main_loop()
