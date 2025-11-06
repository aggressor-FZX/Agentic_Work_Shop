# tasks.py
from __future__ import annotations
import os
from typing import List, Dict, Any
from celery import shared_task

# Import your LangGraph entry point
from manager import run_manager

@shared_task(name="tasks.run_manager_task", bind=True)
def run_manager_task(self, goal: str, scope: List[str] = None, iterations: int = 3) -> Dict[str, Any]:
    """
    Runs the LangGraph manager in-process (faster than spawning a subprocess).
    Returns a compact summary dict for dashboards or TaskMaster.
    """
    scope = scope or ["src", "tests"]

    # Optionally inject unified memory namespace per task:
    os.environ.setdefault("UM_NAMESPACE", os.getenv("UM_NAMESPACE", "Agentic_Work_Shop"))

    # Run your manager (must be pure function side effects: git apply, tests, logs)
    # Modify run_manager to return a small dict if it currently prints only.
    result = run_manager(goal, scope, max_iter=iterations)  # adapt if signature differs

    # If your run_manager prints and not returns, you can build/collect a small result here
    # e.g., read manager.log tail or have run_manager return { state, plan_tail, test_result }
    # We'll just return a simple OK marker for now:
    return {
        "state": "DONE",
        "goal": goal,
        "scope": scope,
        "iterations": iterations,
    }
