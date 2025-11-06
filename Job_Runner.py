#!/usr/bin/env python3
# Job_Runner.py – Enqueue and monitor tasks in TaskMaster from Python
import os, sys, json, time, socket, subprocess, uuid, shutil, signal, pathlib

PROJECT = pathlib.Path(__file__).resolve().parent
TM_QUEUE = PROJECT/".taskmaster/queue.json"
TM_STATUS = PROJECT/".taskmaster/status.json"
TM_PORT = 7157  # your taskmaster --server port
VENV_BIN = PROJECT/".venv/bin"
PY = str(VENV_BIN/"python") if (VENV_BIN/"python").exists() else shutil.which("python") or "python"

def port_open(host="127.0.0.1", port=TM_PORT, timeout=0.25):
    with socket.socket() as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False

def ensure_redis():
    # Best-effort; ok if already running
    subprocess.run(["sudo","service","redis-server","start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def ensure_taskmaster():
    if port_open(): return
    # Prefer project launcher you created earlier
    cmd = ["npm","run","taskmaster:server"]
    subprocess.Popen(cmd, cwd=str(PROJECT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # wait a bit for server
    for _ in range(30):
        if port_open(): return
        time.sleep(0.2)
    raise RuntimeError("TaskMaster server did not open port 7157")

def ensure_bridge():
    # Run your Python bridge that spawns LangGraph workers in parallel
    # Use a pidfile to avoid dupes
    pidfile = PROJECT/".taskmaster/bridge.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, 0)  # check alive
            return
        except Exception:
            pass
    # Load .env file if it exists
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT/".env")
    except ImportError:
        pass  # dotenv not installed, continue with current env
    env = os.environ.copy() # set up env for venv 
    env.setdefault("OPENAI_BASE_URL","https://openrouter.ai/api/v1")
    # activate venv implicitly by calling venv python
    p = subprocess.Popen([PY, "taskmaster_bridge.py"], cwd=str(PROJECT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(p.pid))

def enqueue(goal: str, scope=None, iterations=3):
    scope = scope or ["src","tests"]
    q = []
    if TM_QUEUE.exists():
        try:
            q = json.loads(TM_QUEUE.read_text() or "[]")
        except Exception:
            q = []
    tid = str(uuid.uuid4())
    q.append({"id": tid, "goal": goal, "scope": scope, "iterations": iterations})
    TM_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    TM_QUEUE.write_text(json.dumps(q, indent=2))
    return tid

def wait_for(tid: str, timeout_sec=1800):
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if TM_STATUS.exists():
            try:
                st = json.loads(TM_STATUS.read_text() or "{}")
            except Exception:
                st = {}
            if tid in st and st[tid].get("state") in {"DONE","ERROR"}:
                return st[tid]
        time.sleep(0.5)
    return {"state":"TIMEOUT"}

def main():
    if len(sys.argv) < 2:
        print("Usage: python myscript.py \"<goal>\" [scope1 scope2 ...] [--iters N]")
        sys.exit(1)
    goal = sys.argv[1]
    rest = sys.argv[2:]
    iters = 3
    if "--iters" in rest:
        i = rest.index("--iters")
        iters = int(rest[i+1]); rest = rest[:i] + rest[i+2:]
    scope = rest or ["src","tests"]

    ensure_redis()
    ensure_taskmaster()
    ensure_bridge()
    tid = enqueue(goal, scope, iters)
    print(f"Queued task {tid} → goal: {goal} scope: {scope} iters: {iters}")
    result = wait_for(tid, timeout_sec=3600)
    state = result.get("state")
    print(f"\n=== RESULT: {state} ===")
    if "stdout_tail" in result:
        print(result["stdout_tail"][-800:])
    if "stderr_tail" in result and result["stderr_tail"]:
        print("\n[stderr]\n", result["stderr_tail"][-400:])

if __name__ == "__main__":
    main()
