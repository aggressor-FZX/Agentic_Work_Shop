# worker_process.py
import time
import os
from Orchistrate import worker_node, log_event
from shared import OrchestratorState

def main():
    # Get the workspace path from an environment variable to ensure the worker operates in the correct directory
    workspace_path = os.getenv("ORCHESTRATOR_WORKSPACE")
    if not workspace_path or not os.path.isdir(workspace_path):
        print("Error: ORCHESTRATOR_WORKSPACE environment variable not set or invalid.")
        return

    os.chdir(workspace_path)
    log_event("worker_process_start", f"Starting worker process in {workspace_path}", agent="worker_process")
    
    # Initial state is minimal, as the worker gets its context from the dequeued task
    initial_state = OrchestratorState(
        goal="",
        target_paths=[],
        plan=None,
        patch=None,
        test_result=None,
        test_log=None,
        iterations=0,
        current_task_id=None,
        task_dependencies=[],
        sub_tasks=[]
    )
    
    while True:
        # The worker_node will dequeue a task and process it.
        # It uses a blocking pop, so it will wait for a task.
        worker_node(initial_state)
        # A short sleep to prevent a tight loop in case of unexpected errors
        time.sleep(1)

if __name__ == "__main__":
    main()
