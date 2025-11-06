#!/usr/bin/env python3
"""
manager.py – Autonomous LangGraph Orchestrator with Supervisor Shell
"""
from fastmcp import FastMCP

from __future__ import annotations
import os, sys, json, textwrap, subprocess, threading, queue, time, re, shlex
from typing import List, Literal, TypedDict, Optional
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

# === CONFIGURATION ===
LOG_FILE = "manager.log"
OPENROUTER_BASE = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("OPENAI_API_KEY")
assert API_KEY, "Set OPENAI_API_KEY to your OpenRouter key."

def llm(model: str, temperature: float = 0.2):
    return ChatOpenAI(
        base_url=OPENROUTER_BASE,
        api_key=API_KEY,
        model=model,
        temperature=temperature,
        default_headers={"HTTP-Referer": "http://localhost", "X-Title": "LangGraph-Orchestrator"}
    )

# === LOGGING UTIL ===
def log_event(event: str, text: str):
    line = f"[{time.strftime('%H:%M:%S')}] {event.upper()}: {text.strip()[:5000]}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# === TOOLING (safe runner) ===
ALLOWED_TOOLS = {
    # Python
    "pip_install": lambda pkgs: [sys.executable, "-m", "pip", "install", *pkgs],
    "pytest":      lambda args: ["pytest", "-q", *args],           # e.g., ["-k","foo"]
    "ruff":        lambda args: ["ruff", *(args or ["."])],
    "black_check": lambda args: ["black", "--check", *(args or ["."])],
    "flake8":      lambda args: ["flake8", *(args or ["."])],
    "mypy":        lambda args: ["mypy", *(args or ["."])],
    # Node/JS
    "npm_install": lambda pkgs: ["npm", "install", *pkgs],
    "npm_test":    lambda args: ["npm", "test", "--silent", *args],
    # C/C++
    "cmake_build": lambda args: ["cmake", "--build", *(args or ["."])],
    "ctest":       lambda args: ["ctest", "-j2", "--output-on-failure", *args],
    # Git helpers (no network by default)
    "git_status":  lambda _: ["git", "status", "--porcelain"],
}

# --- Memory Store Tools ---
from memory_store import UnifiedMemory
UM = UnifiedMemory(ns="dev")  # choose a namespace per project/branch

ALLOWED_TOOLS.update({
    "mem_put":  lambda args: ["__PY__", "mem_put", *args],     # mem_put key JSON
    "mem_get":  lambda args: ["__PY__", "mem_get", *args],     # mem_get key
    "mem_del":  lambda args: ["__PY__", "mem_del", *args],     # mem_del key
    "mem_search": lambda args: ["__PY__", "mem_search", *args] # mem_search "query" topk
})

def run_tool(tool: str, args_or_pkgs=None, cwd=".", timeout=600):
    if tool not in ALLOWED_TOOLS:
        return (1, f"DENIED: tool '{tool}' not in allowlist", "")
    args = args_or_pkgs or []
    spec = ALLOWED_TOOLS[tool](args)
    if spec[0] == "__PY__":
        op = spec[1]
        if op == "mem_put":
            key, j = args[0], " ".join(args[1:])
            rc = UM.put(key, json.loads(j))
            return (0, rc, "")
        if op == "mem_get":
            key = args[0]
            val = UM.get(key)
            return (0, json.dumps(val or {}), "")
        if op == "mem_del":
            key = args[0]
            ok = UM.delete(key)
            return (0, "OK" if ok else "MISS", "")
        if op == "mem_search":
            query = args[0]
            topk = int(args[1]) if len(args) > 1 else 5
            hits = UM.search(query, topk=topk)
            # compact print
            return (0, json.dumps([{"key":k,"score":s,"value":v} for k,s,v in hits]), "")
        return (1, "mem_op_unknown", "")
    # shell tools handled as before…
    proc = subprocess.run(spec, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return (proc.returncode, proc.stdout, proc.stderr)
# ===

# === TOOL REQUEST PARSER ===
TOOL_RE = re.compile(r"^TOOL:\s*([a-zA-Z0-9_]+)\s*(.*)$")

def parse_tool_requests(text: str):
    """
    Returns list of (tool_name, args_list)
    Where args_list is shell-split: e.g., "numpy==2.0.1 pandas" -> ["numpy==2.0.1","pandas"]
    """
    requests = []
    for line in text.splitlines():
        m = TOOL_RE.match(line.strip())
        if m:
            tool = m.group(1)
            rest = m.group(2).strip()
            # split shell-style but without executing
            args = shlex.split(rest) if rest else []
            requests.append((tool, args))
    return requests

# === STATE ===
class OrchestratorState(TypedDict):
    goal: str
    target_paths: List[str]
    plan: Optional[str]
    patch: Optional[str]
    test_result: Optional[str]
    test_log: Optional[str]
    iterations: int

# === AGENTS ===
def plan_node(state: OrchestratorState):
    planner = llm("deepseek-v3")
    prompt = f"""Plan steps to achieve:
Goal: {state['goal']}
Scope: {state['target_paths']}"""
    plan = planner.invoke(prompt).content.strip()
    log_event("plan", plan)
    return {**state, "plan": plan}

def env_node(state: OrchestratorState):
    # Ask planner to declare minimal tools/deps needed in TOOL: lines
    planner = llm("deepseek-v3")
    prompt = f"""
You can request tools strictly using 'TOOL: <name> <args>'.
Allowed tools: pip_install, npm_install, pytest, ruff, black_check, flake8, mypy, cmake_build, ctest.
Goal: {state['goal']}
Repo paths: {state['target_paths']}

Output only TOOL: lines for dependencies to install or basic setup steps.
If none needed, output nothing.
Examples:
TOOL: pip_install pytest ruff
TOOL: npm_install jest
"""
    plan_tools = planner.invoke(prompt).content
    log_event("env_tools", plan_tools.strip() or "<none>")

    for tool, args in parse_tool_requests(plan_tools):
        rc, out, err = run_tool(tool, args)
        log_event("env_exec", f"{tool} {args} -> rc={rc}\n{out[-800:]}\n{err[-800:]}")
    return state

def code_node(state: OrchestratorState):
    coder = llm("grok-4-fast", 0.1)
    prompt = f"""
Goal: {state['goal']}
Plan:
{state['plan']}
Touch only {state['target_paths']}
Return a single unified diff, no prose.
"""
    patch = coder.invoke(prompt).content
    log_event("patch", patch[:600])
    return {**state, "patch": patch}

def apply_patch(patch: str):
    if not patch:
        return "EMPTY_PATCH"
    proc = subprocess.run(["git", "apply", "--whitespace=fix", "-p0", "-"],
                          input=patch.encode(), capture_output=True)
    if proc.returncode != 0:
        return f"APPLY_FAIL\n{proc.stderr.decode()[:1000]}"
    return "APPLY_OK"
    
# --- Auto-install missing imports ----------------------------------
MISSING_RE = re.compile(r"ModuleNotFoundError:\s*No module named '([^']+)'")

def try_autoinstall_missing(state: OrchestratorState):
    log = state.get("test_log") or ""
    m = MISSING_RE.search(log)
    if not m:
        return False, "no_missing"
    pkg = m.group(1)
    rc, out, err = run_tool("pip_install", [pkg])
    log_event("auto_pip", f"pip install {pkg} -> rc={rc}\n{out[-400:]}\n{err[-400:]}")
    return (rc == 0), pkg

def test_node(state: OrchestratorState):
    status = apply_patch(state["patch"] or "")
    if "FAIL" in status:
        log_event("apply_fail", status)
        return {**state, "test_result": "FAIL", "test_log": status}

    rc, out, err = run_tool("pytest", [])
    passed = (rc == 0)
    tail = f"STDOUT:\n{out[-1000:]}\nSTDERR:\n{err[-1000:]}"
    if not passed:
        ok, pkg = try_autoinstall_missing({**state, "test_log": tail})
        if ok:
            # re-run tests once after auto-install
            rc2, out2, err2 = run_tool("pytest", [])
            passed = (rc2 == 0)
            tail = f"STDOUT:\n{out2[-1000:]}\nSTDERR:\n{err2[-1000:]}"
    log_event("tests", ("PASS" if passed else "FAIL") + "\n" + tail)
    return {**state, "test_result": "PASS" if passed else "FAIL", "test_log": tail}

def review_node(state: OrchestratorState):
    reviewer = llm("deepseek-v3")
    prompt = f"""
Tests: {state['test_result']}
Logs (tail): {state['test_log'][:2000]}

If FAIL: 
- Provide one-sentence reason, 
- then (optionally) lines starting with 'TOOL:' to install deps or run a specific allowed tool, 
- then finish with either:
  ACTION:ITERATE <one-line next change>
If PASS:
  ACTION:FINALIZE

Allowed tools: pip_install, npm_install, pytest, ruff, black_check, flake8, mypy, cmake_build, ctest.
"""
    verdict = reviewer.invoke(prompt).content.strip()
    log_event("review", verdict)

    # Execute any TOOL requests the reviewer emitted
    for tool, args in parse_tool_requests(verdict):
        rc, out, err = run_tool(tool, args)
        log_event("review_exec", f"{tool} {args} -> rc={rc}\n{out[-600:]}\n{err[-600:]}")
    return {**state, "plan": f"{state.get('plan','')}\nReviewer: {verdict}"}

def should_iterate(state: OrchestratorState) -> Literal["iterate","finish"]:
    if state["iterations"] >= 3:
        return "finish"
    last = (state.get("plan") or "").splitlines()[-1].upper()
    if "FINALIZE" in last and state.get("test_result") == "PASS":
        return "finish"
    return "iterate"

# === BUILD GRAPH ===
workflow = StateGraph(OrchestratorState)
workflow.add_node("plan", plan_node)
workflow.add_node("code", code_node)
workflow.add_node("env", env_node)      # <— NEW
workflow.add_node("test", test_node)
workflow.add_node("review", review_node)
workflow.set_entry_point("plan")
workflow.add_edge("plan", "code")
workflow.add_edge("code", "env")      # <— NEW
workflow.add_edge("env", "test")      # <— NEW
workflow.add_edge("test", "review")
workflow.add_conditional_edges("review", should_iterate, {"iterate": "code", "finish": END})
graph = workflow.compile()

# === SUPERVISOR SHELL ===
def supervisor_shell(cmd_q: queue.Queue):
    print("\n🧠 Supervisor shell ready. Type 'help' for commands.\n")
    while True:
        try:
            line = input("manager> ").strip()
        except (EOFError, KeyboardInterrupt):
            cmd_q.put("quit"); return
        if not line: continue
        if line in {"quit","exit"}: cmd_q.put("quit"); return
        if line in {"pause","status","resume"}: cmd_q.put(line); continue
        if line == "help":
            print("Commands: status | pause | resume | quit")
        else:
            print("Unknown command.")

# === MAIN LOOP ===
def run_manager(goal, scope_paths, max_iter=5):
    # ... (rest of the function)
    return {"status": "completed", "iterations": max_iter}

if __name__ == "__main__":
    import sys
    goal = sys.argv[1] if len(sys.argv) > 1 else "Refactor src/foo; keep tests green."
    scopes = sys.argv[2:-1] if len(sys.argv) > 3 and sys.argv[-1].isdigit() else sys.argv[2:]
    iters = int(sys.argv[-1]) if (len(sys.argv) > 2 and sys.argv[-1].isdigit()) else 3
    if not scopes: scopes = ["src","tests"]
    result = run_manager(goal, scopes, max_iter=iters)
    print("[result]", result)
