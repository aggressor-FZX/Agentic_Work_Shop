#!/usr/bin/env python3
"""
manager.py – Autonomous LangGraph Orchestrator with Supervisor Shell
"""
from __future__ import annotations
import os, sys, json, textwrap, subprocess, threading, queue, time, re, shlex
from typing import List, Literal, TypedDict, Optional

from shared import OrchestratorState, get_llm, log_event

from langgraph.graph import StateGraph, END

# --- Research Tool Pack (safe HTTP + search) ---
import re, json, time, html
import requests
from bs4 import BeautifulSoup
from readability import Document

HTTP_TIMEOUT = 12
MAX_BYTES = 800_000  # ~0.8MB per fetch
ALLOWED_SCHEMES = ("http://", "https://")
USER_AGENT = "AgenticWorkshop/1.0 (+research bot)"

def ddg_search(query: str, max_results=5):
    # DuckDuckGo HTML lite endpoint (no API key). Fallback if rate-limited.
    url = "https://duckduckgo.com/html/"
    params = {"q": query}
    r = requests.post(url, data=params, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for a in soup.select(".result__a")[:max_results]:
        href = a.get("href")
        title = a.get_text(" ", strip=True)
        if href and href.startswith(("http://","https://")):
            out.append({"title": title, "url": href})
    return out

def http_fetch(url: str, max_bytes=MAX_BYTES):
    if not url.startswith(ALLOWED_SCHEMES) or "javascript:" in url.lower():
        return {"error":"blocked scheme"}
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT, stream=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type","")
    # allow only text/html for readability extraction
    if "html" not in ctype.lower():
        # still allow small text
        text = r.text[: min(MAX_BYTES, 200_000)]
        return {"url": url, "title": url, "content": text, "note":"non-html content"}
    # stream-limit
    acc = bytearray()
    for chunk in r.iter_content(8192):
        acc.extend(chunk)
        if len(acc) > max_bytes:
            break
    html_bytes = bytes(acc)
    doc = Document(html_bytes)
    title = doc.short_title()
    article_html = doc.summary()
    # crude text extraction
    soup = BeautifulSoup(article_html, "lxml")
    article_text = soup.get_text("\n", strip=True)
    return {"url": url, "title": title, "content": article_text[:30000]}

def arxiv_search(q: str, max_results=5):
    import arxiv as ax
    results = ax.Search(query=q, max_results=max_results, sort_by=ax.SortCriterion.Relevance).results()
    out = []
    for r in results:
        out.append({
            "title": r.title,
            "url": r.entry_id,
            "published": r.published.strftime("%Y-%m-%d"),
            "summary": r.summary[:2000]
        })
    return out

# === CONFIGURATION ===

from langchain_core.callbacks import BaseCallbackHandler

class TokenUsageCallback(BaseCallbackHandler):
    def __init__(self):
        self.total_tokens = 0

    def on_llm_end(self, response, **kwargs):
        if response.llm_output and 'token_usage' in response.llm_output:
            self.total_tokens += response.llm_output['token_usage'].get('total_tokens', 0)

token_callback = TokenUsageCallback()

# === PATCH UTIL ===
def apply_patch(patch_content: str) -> str:
    """Apply a unified diff patch using the patch command."""
    import tempfile
    import subprocess
    
    # Write patch to temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False) as f:
        f.write(patch_content)
        patch_file = f.name
    
    try:
        # Apply the patch
        result = subprocess.run(['patch', '-p1', '--input', patch_file], 
                              capture_output=True, text=True, cwd='.')
        if result.returncode == 0:
            return "SUCCESS: Patch applied successfully"
        else:
            return f"FAIL: {result.stderr.strip()}"
    except FileNotFoundError:
        return "FAIL: patch command not found. Please install patch utility."
    finally:
        # Clean up temp file
        os.unlink(patch_file)

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
    "git_branch":  lambda args: ["git", "branch", *args],
    "git_checkout": lambda args: ["git", "checkout", *args],
    "git_add":     lambda args: ["git", "add", *args],
    "git_commit":  lambda args: ["git", "commit", *args],
}

# --- Memory Store Tools ---
from memory_store import UnifiedMemory
UM = UnifiedMemory(ns="dev")  # choose a namespace per project/branch

ALLOWED_TOOLS.update({
    "mem_put":  lambda args: ["__PY__", "mem_put", *args],     # mem_put key JSON
    "mem_get":  lambda args: ["__PY__", "mem_get", *args],     # mem_get key
    "mem_del":  lambda args: ["__PY__", "mem_del", *args],     # mem_del key
    "mem_search": lambda args: ["__PY__", "mem_search", *args], # mem_search "query" topk
    "web_search":  lambda args: ["__PY__", "web_search", *args],   # query, [max_results]
    "web_fetch":   lambda args: ["__PY__", "web_fetch", *args],    # url
    "paper_search":lambda args: ["__PY__", "paper_search", *args], # query, [max_results]
    "context7_docs": lambda args: ["__MCP__", "context7", *args],   # Context7 documentation lookup
    "task_create": lambda args: ["__PY__", "task_create", *args],  # task_create id title description dependencies
    "task_update": lambda args: ["__PY__", "task_update", *args],  # task_update id status|field value
    "task_get": lambda args: ["__PY__", "task_get", *args],        # task_get id
    "task_list": lambda args: ["__PY__", "task_list", *args],      # task_list [status]
    "task_expand": lambda args: ["__PY__", "task_expand", *args],  # task_expand prd_text
    "enqueue_task": lambda args: ["__PY__", "enqueue_task", *args], # enqueue_task queue_name task_json
    "dequeue_task": lambda args: ["__PY__", "dequeue_task", *args], # dequeue_task queue_name
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
        # Task management ops
        import datetime
        TASK_FILE = ".track_task/tasks.json"
        def load_tasks():
            try:
                with open(TASK_FILE, "r") as f:
                    return json.load(f)
            except FileNotFoundError:
                return {}
        def save_tasks(tasks):
            os.makedirs(os.path.dirname(TASK_FILE), exist_ok=True)
            with open(TASK_FILE, "w") as f:
                json.dump(tasks, f, indent=2)
        if op == "task_create":
            task_id, title, desc, deps_json = args[0], args[1], " ".join(args[2:-1]), args[-1]
            deps = json.loads(deps_json) if deps_json else []
            tasks = load_tasks()
            if task_id in tasks:
                return (1, "TASK_EXISTS", "")
            tasks[task_id] = {
                "id": task_id,
                "title": title,
                "description": desc,
                "status": "pending",
                "dependencies": deps,
                "created_at": datetime.datetime.now().isoformat(),
                "updated_at": datetime.datetime.now().isoformat()
            }
            save_tasks(tasks)
            return (0, "CREATED", "")
        if op == "task_update":
            task_id, field, value = args[0], args[1], " ".join(args[2:])
            tasks = load_tasks()
            if task_id not in tasks:
                return (1, "TASK_NOT_FOUND", "")
            if field == "status":
                tasks[task_id]["status"] = value
            else:
                tasks[task_id][field] = value
            tasks[task_id]["updated_at"] = datetime.datetime.now().isoformat()
            save_tasks(tasks)
            return (0, "UPDATED", "")
        if op == "task_get":
            task_id = args[0]
            tasks = load_tasks()
            task = tasks.get(task_id)
            return (0, json.dumps(task or {}), "")
        if op == "task_list":
            status_filter = args[0] if args else None
            tasks = load_tasks()
            filtered = [t for t in tasks.values() if not status_filter or t.get("status") == status_filter]
            return (0, json.dumps(filtered), "")
        if op == "enqueue_task":
            queue_name, task_json = args[0], " ".join(args[1:])
            UM.r.lpush(queue_name, task_json)
            return (0, "ENQUEUED", "")
        if op == "dequeue_task":
            queue_name = args[0]
            # Blocking pop with a timeout to avoid waiting forever
            task = UM.r.brpop(queue_name, timeout=5)
            if task:
                return (0, task[1].decode('utf-8'), "")
            return (0, "EMPTY_QUEUE", "")
        if op == "task_expand":
            prd_text = " ".join(args)
            # Use LLM to expand
            expander = llm("anthropic/claude-3-haiku")
            prompt = f"Expand this PRD into detailed tasks with dependencies:\n{prd_text}\n\nOutput JSON array of tasks: [{{id, title, description, dependencies:[]}}]"
            response = expander.invoke(prompt).content.strip()
            try:
                expanded = json.loads(response)
                return (0, json.dumps(expanded), "")
            except:
                return (1, "INVALID_JSON", response)
        try:
            if op == "web_search":
                q = " ".join(args) if args else ""
                hits = ddg_search(q, max_results=5)
                return (0, json.dumps(hits, ensure_ascii=False), "")
            if op == "web_fetch":
                url = args[0]
                doc = http_fetch(url)
                return (0, json.dumps(doc, ensure_ascii=False), "")
            if op == "paper_search":
                q = " ".join(args) if args else ""
                hits = arxiv_search(q, max_results=5)
                return (0, json.dumps(hits, ensure_ascii=False), "")
        except Exception as e:
            return (1, "", f"{type(e).__name__}: {e}")
        return (1, "mem_op_unknown", "")

    # MCP tools (external documentation servers)
    if spec[0] == "__MCP__":
        server = spec[1]  # e.g., "context7"
        query = " ".join(args) if args else ""
        try:
            if server == "context7":
                # Call Context7 MCP server
                output = call_context7(query)
                return (0, output, "")
        except Exception as e:
            return (1, "", f"MCP {server} error: {e}")
        return (1, "", f"unknown MCP server: {server}")

    # External shell tools (pip, pytest, etc.) handled as before…
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


# === AGENTS ===
def task_manager_node(state: OrchestratorState):
    expander = get_llm('pm')
    prompt = f"""Analyze this goal/PRD and break it into detailed, actionable tasks with dependencies.

Goal: {state['goal']}
Target Paths: {state['target_paths']}

Output a JSON array of tasks, each with:
- id: unique string identifier
- title: short title
- description: detailed description
- dependencies: array of task ids this depends on (empty for independent tasks)

Ensure tasks are ordered logically, with foundational tasks first.

Example:
[
  {{"id": "setup_env", "title": "Set up development environment", "description": "Install required dependencies and set up project structure", "dependencies": []}},
  {{"id": "design_api", "title": "Design API endpoints", "description": "Define REST API structure and data models", "dependencies": ["setup_env"]}}
]

Only output the JSON array, no other text."""
    response = expander.invoke(prompt).content.strip()
    try:
        tasks = json.loads(response)
        # Create tasks in the system
        for task in tasks:
            deps_json = json.dumps(task["dependencies"])
            rc, out, err = run_tool("task_create", [task["id"], task["title"], task["description"], deps_json])
            if rc != 0:
                log_event("task_create_error", f"Failed to create task {task['id']}: {out} {err}")
        # Set current task to the first one with no dependencies
        first_task = next((t for t in tasks if not t["dependencies"]), tasks[0] if tasks else None)
        current_id = first_task["id"] if first_task else None
        deps = [t["id"] for t in tasks if t["id"] != current_id and current_id in t.get("dependencies", [])]
        log_event("task_manager", f"Created {len(tasks)} tasks, starting with {current_id}", agent="task_manager")
        return {**state, "current_task_id": current_id, "task_dependencies": deps}
    except json.JSONDecodeError as e:
        log_event("task_expand_error", f"Invalid JSON from LLM: {response}")
        return {**state, "current_task_id": None, "task_dependencies": []}

def plan_node(state: OrchestratorState):
    planner = get_llm('pm')
    prompt = f"""Plan steps to achieve:
Goal: {state['goal']}
Scope: {state['target_paths']}

If domain knowledge is missing or uncertain, FIRST do research:
- Use: 
  TOOL: web_search <query>
  TOOL: web_fetch <url>
  TOOL: paper_search <query>    # for academic topics
  TOOL: context7_docs <library query>  # for up-to-date library/tool docs
- Summarize findings into unified memory:
  TOOL: mem_put research/context {{"text":"<short summary>", "sources":[...]}}
- Then continue planning and coding.

Always cite 2–5 sources (store in memory) before proposing architecture in unfamiliar domains."""
    plan = planner.invoke(prompt).content.strip()
    log_event("plan", plan, agent="planner")
    return {**state, "plan": plan}

def research_node(state: OrchestratorState):
    planner = get_llm('pm')  # or your preferred planner model
    prompt = f"""
Goal: {state['goal']}
Known constraints: {state.get('constraints','(none)')}
Current plan (if any): {state.get('plan','(none)')}

If domain knowledge might be insufficient, emit only TOOL lines to:
- web_search "<query>"
- web_fetch "<url>"
- paper_search "<query>"
- context7_docs "<library or toolkit query>"  # For up-to-date library docs

For libraries/toolkits that may have changed recently (past 12 months), use context7_docs first.
Examples:
TOOL: context7_docs "React Query v5 invalidate API"
TOOL: context7_docs "Next.js 14 app router changes"

Optionally store a short synthesis:
- mem_put research/context {{"text":"...", "sources":[...]}}
If no research needed, emit nothing.
"""
    out = planner.invoke(prompt).content
    for tool, args in parse_tool_requests(out):
        rc, so, se = run_tool(tool, args)
        log_event("research_tool", f"{tool} {args}\nRC={rc}\n{so[:800]}\n{se[:400]}", agent="researcher")
        # Encourage storing condensed notes
    return state

def env_node(state: OrchestratorState):
    # Ask planner to declare minimal tools/deps needed in TOOL: lines
    planner = get_llm('pm')
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
    log_event("env_tools", plan_tools.strip() or "<none>", agent="env_manager")

    for tool, args in parse_tool_requests(plan_tools):
        rc, out, err = run_tool(tool, args)
        log_event("env_exec", f"{tool} {args} -> rc={rc}\n{out[-800:]}\n{err[-800:]}")
    return state

from shared import OrchestratorState

def pm_node(state: OrchestratorState):
    pm = get_llm('pm', temperature=0.1)
    prompt = f"""
You are a Product Manager. Your role is to break down the high-level goal into small, actionable sub-tasks for your team of developers.

Goal: {state['goal']}
Plan:
{state['plan']}

Decompose this into a series of sub-tasks. For each sub-task, provide:
1. A unique, descriptive branch name (e.g., feature/add-user-model).
2. A clear, concise instruction for the developer.

Output this as a JSON array of objects, where each object has a "branch" and "instruction" key.
Example:
[\n    {{\"branch\": \"feature/setup-database\", \"instruction\": \"Set up the initial database schema for users.\"}},\n    {{\"branch\": \"feature/user-api-routes\", \"instruction\": \"Create the API routes for user registration and login.\"}}
]
"""
    sub_tasks_json = pm.invoke(prompt).content
    try:
        sub_tasks = json.loads(sub_tasks_json)
        # Enqueue each task instead of storing in state
        for task in sub_tasks:
            run_tool("git_branch", [task["branch"]])
            task_data = json.dumps({
                "branch": task["branch"],
                "instruction": task["instruction"],
                "goal": state['goal'],
                "target_paths": state['target_paths']
            })
            run_tool("enqueue_task", ["worker_queue", task_data])
        
        log_event("pm", f"Enqueued {len(sub_tasks)} sub-tasks to worker queue.", agent="pm")
        # Return state without sub_tasks since they're now in the queue
        return {**state, "sub_tasks": sub_tasks}  # Keep for monitoring, but workers will dequeue
    except json.JSONDecodeError:
        log_event("pm_error", f"Failed to parse sub-tasks from LLM. Raw response: {sub_tasks_json}", agent="pm")
        return {**state, "sub_tasks": []}

def monitoring_node(state: OrchestratorState):
    """PM monitors the results queue for completed tasks."""
    # Check for completed tasks in results queue
    result = run_tool("dequeue_task", ["results_queue"])
    if result[0] != 0 or result[1] == "EMPTY_QUEUE":
        log_event("pm_monitor", "No completed tasks in results queue.", agent="pm")
        return state
    
    try:
        result_data = json.loads(result[1])
        branch = result_data["branch"]
        instruction = result_data["instruction"]
        status = result_data["status"]
        
        if status == "success":
            log_event("pm_success", f"Task completed: {branch} - {instruction}", agent="pm")
            # Could merge branch or mark as complete
            run_tool("git_checkout", ["main"])  # Switch back to main
            # TODO: Implement branch merging logic
        else:
            log_event("pm_failure", f"Task failed: {branch} - {result_data.get('error', 'Unknown error')}", agent="pm")
            # Could re-queue failed tasks or handle differently
        
        return {**state, "last_result": result_data}
    except (json.JSONDecodeError, KeyError) as e:
        log_event("pm_monitor_error", f"Invalid result data: {e}", agent="pm")
        return state

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
    log_event("tests", ("PASS" if passed else "FAIL") + "\n" + tail, agent="tester")
    return {**state, "test_result": "PASS" if passed else "FAIL", "test_log": tail}

def review_node(state: OrchestratorState):
    reviewer = get_llm('pm')
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
    log_event("review", verdict, agent="reviewer")

    # Execute any TOOL requests the reviewer emitted
    for tool, args in parse_tool_requests(verdict):
        rc, out, err = run_tool(tool, args)
        log_event("review_exec", f"{tool} {args} -> rc={rc}\n{out[-600:]}\n{err[-600:]}")
    return {**state, "plan": f"{state.get('plan','')}\nReviewer: {verdict}"}

def task_complete_node(state: OrchestratorState):
    if state.get("current_task_id") and state.get("test_result") == "PASS" and "FINALIZE" in (state.get("plan") or "").upper():
        run_tool("task_update", [state["current_task_id"], "status", "completed"])
        log_event("task_completed", state["current_task_id"])
        # Find next task
        rc, out, err = run_tool("task_list", ["pending"])
        if rc == 0:
            pending = json.loads(out)
            completed_ids = {t["id"] for t in json.loads(run_tool("task_list", ["completed"])[1])}
            next_task = next((t for t in pending if all(d in completed_ids for d in t.get("dependencies", []))), None)
            if next_task:
                deps = [t["id"] for t in pending if next_task["id"] in t.get("dependencies", [])]
                return {**state, "current_task_id": next_task["id"], "task_dependencies": deps, "iterations": 0, "plan": None, "patch": None, "test_result": None, "test_log": None}
    return state

def should_iterate(state: OrchestratorState) -> Literal["iterate","finish"]:
    # For monitoring node: check if all tasks are completed
    sub_tasks = state.get("sub_tasks", [])
    if not sub_tasks:
        return "finish"  # No tasks to monitor
    
    # Check results queue for completed tasks
    completed_branches = set()
    while True:
        result = run_tool("dequeue_task", ["results_queue"])
        if result[0] != 0 or result[1] == "EMPTY_QUEUE":
            break
        try:
            result_data = json.loads(result[1])
            if result_data["status"] == "success":
                completed_branches.add(result_data["branch"])
        except (json.JSONDecodeError, KeyError):
            continue
    
    # If all branches are completed, finish
    task_branches = {task["branch"] for task in sub_tasks}
    if task_branches.issubset(completed_branches):
        return "finish"
    
    # Otherwise continue monitoring (with timeout)
    if state["iterations"] >= 10:  # Allow more time for parallel tasks
        return "finish"
    
    return "iterate"

# === BUILD GRAPH ===
workflow = StateGraph(OrchestratorState)
workflow.add_node("plan", plan_node)
workflow.add_node("research", research_node)
workflow.add_node("pm", pm_node)
workflow.add_node("monitor", monitoring_node)
workflow.add_node("env", env_node)
workflow.add_node("test", test_node)
workflow.add_node("review", review_node)

workflow.set_entry_point("plan")
workflow.add_edge("plan", "research")
workflow.add_edge("research", "pm")
# After PM enqueues tasks, go to monitoring
workflow.add_edge("pm", "monitor")
# Monitoring loop - check for results and continue monitoring
workflow.add_conditional_edges("monitor", should_iterate, {"iterate": "monitor", "finish": END})
# Workers can run independently (in parallel processes)
# The env/test/review nodes remain for final validation
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

# === CONTEXT7 MCP CALLER ===
def call_context7(query: str) -> str:
    """Call Context7 MCP server for documentation lookup."""
    import subprocess
    import json
    import time
    
    try:
        # Start the MCP server process
        proc = subprocess.Popen(
            ["npx", "-y", "@upstash/context7-mcp", "--transport", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Initialize the connection
        init_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-workshop", "version": "1.0.0"}
            }
        }
        proc.stdin.write(json.dumps(init_msg) + "\n")
        proc.stdin.flush()
        
        # Read response
        response = proc.stdout.readline()
        if response:
            init_response = json.loads(response)
            if "error" in init_response:
                proc.terminate()
                return f"Context7 init error: {init_response['error']}"
        
        # Send initialized notification
        initialized_msg = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        }
        proc.stdin.write(json.dumps(initialized_msg) + "\n")
        proc.stdin.flush()
        
        # First, resolve library ID using local mapping
        library_id = resolve_library_id(query)
        if not library_id:
            # Fallback to MCP resolve if no mapping found
            resolve_msg = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "resolve-library-id",
                    "arguments": {"libraryName": query}
                }
            }
            proc.stdin.write(json.dumps(resolve_msg) + "\n")
            proc.stdin.flush()
            
            # Read resolve response
            resolve_response = proc.stdout.readline()
            print(f"DEBUG: Resolve response: {resolve_response}", file=sys.stderr)
            if resolve_response:
                resolve_data = json.loads(resolve_response)
                if "error" in resolve_data:
                    proc.terminate()
                    return f"Context7 resolve error: {resolve_data['error']}"
                elif "result" in resolve_data:
                    libraries = resolve_data["result"].get("content", [])
                    if libraries and len(libraries) > 0:
                        # Parse the text to find the best library ID
                        text = libraries[0].get("text", "")
                        # Simple parsing - look for the first library ID
                        import re
                        id_match = re.search(r'Context7-compatible library ID: (/[^\n]+)', text)
                        if id_match:
                            library_id = id_match.group(1).strip()
                        else:
                            library_id = f"/{query.replace(' ', '').lower()}/docs"
                    else:
                        library_id = f"/{query.replace(' ', '').lower()}/docs"
            else:
                library_id = f"/{query.replace(' ', '').lower()}/docs"
        
        # Now get documentation
        docs_msg = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get-library-docs",
                "arguments": {
                    "context7CompatibleLibraryID": library_id,
                    "topic": query,
                    "tokens": 5000
                }
            }
        }
        proc.stdin.write(json.dumps(docs_msg) + "\n")
        proc.stdin.flush()
        
        # Read docs response
        docs_response = proc.stdout.readline()
        proc.terminate()
        
        if docs_response:
            docs_data = json.loads(docs_response)
            if "error" in docs_data:
                return f"Context7 docs error: {docs_data['error']}"
            elif "result" in docs_data:
                content = docs_data["result"].get("content", [])
                if content and len(content) > 0:
                    return content[0].get("text", "No documentation content found")
                else:
                    return "No documentation content found"
        
        return "Context7 call completed but no response received"
        
    except subprocess.TimeoutExpired:
        return "Context7 timeout"
    except Exception as e:
        return f"Context7 call failed: {e}"

def resolve_library_id(library_name: str) -> str:
    """Resolve library name to Context7-compatible ID."""
    # Common library mappings
    mappings = {
        "react query": "/websites/tanstack_query_v5",
        "tanstack query": "/websites/tanstack_query_v5",
        "next.js": "/vercel/next.js", 
        "nextjs": "/vercel/next.js",
        "react": "/facebook/react",
        "vue": "/vuejs/vue",
        "angular": "/angular/angular",
        "express": "/expressjs/express",
        "fastify": "/fastify/fastify",
        "prisma": "/prisma/prisma",
        "mongoose": "/automattic/mongoose",
        "axios": "/axios/axios",
        "lodash": "/lodash/lodash",
        "moment": "/moment/moment",
        "jest": "/facebook/jest",
        "webpack": "/webpack/webpack",
        "babel": "/babel/babel",
        "typescript": "/microsoft/typescript",
        "eslint": "/eslint/eslint",
        "prettier": "/prettier/prettier"
    }
    
    # Try exact matches first
    name_lower = library_name.lower().strip()
    if name_lower in mappings:
        return mappings[name_lower]
    
    # Try partial matches
    for key, value in mappings.items():
        if key in name_lower:
            return value
    
    # Fallback to constructed ID
    return f"/{library_name.replace(' ', '').lower()}/docs"

def get_library_docs(library_id: str, topic: str) -> str:
    """Get documentation for a library."""
    try:
        # Use the mcp_context7_get-library-docs tool
        # This is a placeholder - in reality, we'd call the tool
        return f"Documentation for {library_id} regarding {topic}. (This is a mock response - actual integration pending)"
    except Exception as e:
        return f"Failed to get docs: {e}"

# === MAIN LOOP ===
def run_manager(goal, scope_paths, max_iter=5):
    # Create a unique workspace directory for this run
    workspace_name = re.sub(r'\W+', '_', goal.lower())[:50]
    workspace_path = os.path.join(os.getcwd(), "dev_workspaces", workspace_name)
    os.makedirs(workspace_path, exist_ok=True)
    log_event("workspace", f"Created workspace: {workspace_path}")

    # All file operations should be relative to this workspace
    os.chdir(workspace_path)

    initial_state = OrchestratorState(
        goal=goal,
        target_paths=scope_paths,
        plan=None,
        patch=None,
        test_result=None,
        test_log=None,
        iterations=0,
        current_task_id=None,
        task_dependencies=[]
    )
    
    # Run the compiled graph
    final_state = graph.invoke(initial_state)
    
    return {
        "status": "completed",
        "iterations": final_state.get("iterations", max_iter),
        "final_plan": final_state.get("plan"),
        "test_result": final_state.get("test_result"),
        "test_log": final_state.get("test_log")
    }

if __name__ == "__main__":
    import sys
    import subprocess
    import atexit

    goal = sys.argv[1] if len(sys.argv) > 1 else "Refactor src/foo; keep tests green."
    scopes = sys.argv[2:-1] if len(sys.argv) > 3 and sys.argv[-1].isdigit() else sys.argv[2:]
    iters = int(sys.argv[-1]) if (len(sys.argv) > 2 and sys.argv[-1].isdigit()) else 3
    if not scopes: scopes = ["src","tests"]
    
    # Create a unique workspace directory for this run to avoid conflicts
    workspace_name = re.sub(r'\W+', '_', goal.lower())[:50]
    workspace_path = os.path.join(os.getcwd(), "dev_workspaces", workspace_name)
    os.makedirs(workspace_path, exist_ok=True)

    # Start the dashboard server, passing the workspace path
    dashboard_proc = subprocess.Popen([sys.executable, "dashboard/app.py", "--workspace", workspace_path])
    atexit.register(dashboard_proc.terminate)
    
    original_cwd = os.getcwd()
    try:
        result = run_manager(goal, scopes, max_iter=iters)
    finally:
        os.chdir(original_cwd)
        # Write final token count to a log file in the workspace for the dashboard
        token_log_path = os.path.join(workspace_path, "token_usage.log")
        with open(token_log_path, "w") as f:
            f.write(str(token_callback.total_tokens))

    print("[result]", result)
