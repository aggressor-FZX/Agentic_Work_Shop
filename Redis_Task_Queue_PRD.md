# PRD: Project Redis Task Queue - Scaling Agentic Workflows

## 1. Introduction

Our current agentic system manages a multi-step workflow by passing a list of sub-tasks through the LangGraph state. This approach is functional for sequential execution but presents a significant bottleneck for scalability. It tightly couples the Product Manager (PM) agent to the worker agents and does not allow for parallel execution of tasks.

This document outlines the plan to re-architect our system to use our existing Redis infrastructure as a robust, scalable task queue. This will decouple our agents, enable true parallel processing, and increase the overall resilience of the system.

## 2. Goals

*   **Enable Parallel Execution**: Allow multiple worker agents to run simultaneously, each pulling tasks from a central queue.
*   **Decouple Agents**: Remove the direct dependency between the PM and the workers, creating a more modular and scalable architecture.
*   **Increase Resilience**: Ensure that if a worker agent fails, the task it was working on is not lost and can be picked up by another worker.
*   **Improve Performance**: Reduce the end-to-end time for complex projects by processing independent tasks in parallel.

## 3. Key Features

### 3.1. Task Queuing Tools

We will implement a new set of tools that interact directly with Redis lists to manage task queues.

*   **`enqueue_task`**: This tool will take a task object (containing branch, instruction, etc.) and push it onto a designated Redis list (the `worker_queue`).
*   **`dequeue_task`**: This tool will perform a blocking pop from the `worker_queue`, efficiently waiting for a task to become available and then retrieving it.

### 3.2. PM as a "Producer"

The `pm_node` will be refactored. Its primary responsibility will be to decompose the main goal into sub-tasks and then use the `enqueue_task` tool to push each of these tasks onto the `worker_queue`.

### 3.3. Worker as a "Consumer"

The `worker_node` will be simplified. Its core logic will be to call the `dequeue_task` tool to get a job, and then execute it. This makes the worker a generic, stateless consumer of tasks.

### 3.4. Results Queue

To monitor progress, workers will report the status of their completed tasks to a separate Redis list, the `results_queue`. This allows the PM to track which tasks are done and which have failed without directly polling the workers.

### 3.5. PM Monitoring

The PM will have a new state where it monitors the `results_queue`. This allows it to get real-time feedback on the progress of the project, enabling it to perform actions like merging completed branches or re-queuing failed tasks.

## 4. Implementation Checklist

-   [ ] **Create Redis Task Tools**:
    -   [ ] Implement `enqueue_task` function in `Orchistrate.py`.
    -   [ ] Implement `dequeue_task` function in `Orchistrate.py`.
    -   [ ] Add these new tools to the `ALLOWED_TOOLS` dictionary.

-   [ ] **Update Product Manager Agent (`pm_node`)**:
    -   [ ] Remove the logic that stores sub-tasks in the state.
    -   [ ] Add logic to call `enqueue_task` for each sub-task it generates.

-   [ ] **Update Worker Agent (`worker_node`)**:
    -   [ ] Replace the logic that reads tasks from the state with a call to `dequeue_task`.
    -   [ ] Implement logic to push the result (success or failure) to the `results_queue`.

-   [ ] **Update Core Workflow (`StateGraph`)**:
    -   [ ] Create a new `monitoring_node` for the PM to watch the `results_queue`.
    -   [ ] Redesign the graph to loop from the `monitoring_node` back to itself until all tasks are complete.
    -   [ ] Ensure the system can gracefully handle an empty queue and shut down.

## 5. Success Metrics

*   **Parallelism**: The system can successfully run with multiple worker processes, and tasks from the queue are distributed among them.
*   **Resilience**: If a worker is manually stopped mid-task, the task is not lost and can be re-processed.
*   **Performance**: A project with multiple, independent sub-tasks completes significantly faster than the current sequential model.
*   **Clarity**: The logs clearly show tasks being enqueued, dequeued by different workers, and their results being reported.