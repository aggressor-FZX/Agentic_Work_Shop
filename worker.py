from shared import OrchestratorState, get_llm, log_event
from Orchistrate import run_tool, apply_patch
import json

def worker_node(state: OrchestratorState):
    # Dequeue a task from the worker queue
    result = run_tool("dequeue_task", ["worker_queue"])
    if result[0] != 0 or result[1] == "EMPTY_QUEUE":
        log_event("worker_idle", "No tasks available in queue.", agent="worker")
        return state
    
    try:
        task_data = json.loads(result[1])
        branch = task_data["branch"]
        instruction = task_data["instruction"]
        goal = task_data.get("goal", "")
        target_paths = task_data.get("target_paths", [])
    except (json.JSONDecodeError, KeyError) as e:
        log_event("worker_error", f"Invalid task data: {e}", agent="worker")
        return state

    run_tool("git_checkout", [branch])

    for i in range(3): # Retry loop
        try:
            coder = get_llm('worker', worker_type='free', temperature=0.1)
            prompt = f"""
You are a developer. Your task is to write code to solve the following instruction.

Instruction: {instruction}
Target files: {target_paths}

Return a single unified diff of your changes. Do not add any commentary.
"""
            patch = coder.invoke(prompt).content
            log_event("worker_patch", patch[:600], agent="worker")

            apply_status = apply_patch(patch)
            if "FAIL" in apply_status:
                raise ValueError(f"Patch failed to apply: {apply_status}")

            run_tool("git_add", ["."])
            run_tool("git_commit", ["-m", f"feat: {instruction}"])
            
            # Report success to results queue
            result_data = json.dumps({
                "branch": branch,
                "instruction": instruction,
                "status": "success",
                "patch": patch
            })
            run_tool("enqueue_task", ["results_queue", result_data])
            
            return {**state, "patch": patch}
        except Exception as e:
            log_event("worker_error", f"Attempt {i+1} failed: {e}", agent="worker")
            if i == 2:
                # Report failure to results queue
                result_data = json.dumps({
                    "branch": branch,
                    "instruction": instruction,
                    "status": "failed",
                    "error": str(e)
                })
                run_tool("enqueue_task", ["results_queue", result_data])
                return {**state, "test_result": "FAIL", "test_log": str(e)}
    
    return state
