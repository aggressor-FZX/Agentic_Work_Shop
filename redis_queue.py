#!/usr/bin/env python3
"""
Redis task queue management functions
"""
import redis
import json
import os

def enqueue_task(queue_name, task_data):
    """Enqueue a task to the specified Redis queue"""
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=False)
        r.lpush(queue_name, task_data)
        return (0, "TASK_ENQUEUED", "")
    except Exception as e:
        return (1, f"ERROR: {e}", "")

def dequeue_task(queue_name, timeout=5):
    """Dequeue a task from the specified Redis queue (blocking)"""
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=False)
        task = r.brpop(queue_name, timeout=timeout)
        if task:
            return (0, task[1].decode('utf-8'), "")
        return (0, "EMPTY_QUEUE", "")
    except Exception as e:
        return (1, f"ERROR: {e}", "")

def get_queue_depth(queue_name):
    """Get the current depth of a Redis queue"""
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        return r.llen(queue_name)
    except Exception as e:
        print(f"Error getting queue depth: {e}")
        return 0

if __name__ == "__main__":
    # Test the functions
    test_task = {
        "branch": "test_branch",
        "instruction": "Create a hello world function",
        "goal": "Test the queue system",
        "target_paths": ["hello.py"]
    }
    
    # Test enqueue
    result = enqueue_task("worker_queue", json.dumps(test_task))
    print(f"Enqueue result: {result}")
    
    # Test queue depth
    depth = get_queue_depth("worker_queue")
    print(f"Queue depth: {depth}")
    
    # Test dequeue
    result = dequeue_task("worker_queue")
    print(f"Dequeue result: {result}")
