#!/usr/bin/env python3
"""
Autonomous System Test Script
Tests the complete workflow: enqueue → auto-spawn → process → scale down
"""
import os
import sys
import time
import json
import subprocess
import requests
import threading
from pathlib import Path

# Add the current directory to Python path
sys.path.insert(0, '/home/skystarved/cpts_483/Agentic_Work_Shop')

def test_enqueue_task(task_instruction, branch="test_branch"):
    """Enqueue a task and return the result"""
    from enqueue import enqueue_task
    
    task_data = {
        "branch": branch,
        "instruction": task_instruction,
        "goal": f"Test task: {task_instruction}",
        "target_paths": ["test_file.py"]
    }
    
    result = enqueue_task("worker_queue", json.dumps(task_data))
    return result

def test_dashboard_api():
    """Test if the dashboard API is working"""
    try:
        response = requests.get('http://localhost:5001/api/status', timeout=5)
        if response.status_code == 200:
            return True, response.json()
        return False, f"Status code: {response.status_code}"
    except Exception as e:
        return False, str(e)

def test_worker_spawning():
    """Test manual worker spawning via dashboard API"""
    try:
        response = requests.post('http://localhost:5001/api/spawn-worker', timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                return True, result['worker_id']
        return False, response.json()
    except Exception as e:
        return False, str(e)

def monitor_system():
    """Monitor the system for changes"""
    print("\n🔍 Monitoring system for 30 seconds...")
    
    for i in range(30):
        try:
            success, data = test_dashboard_api()
            if success:
                worker_count = data.get('worker_count', 0)
                queue_depth = data.get('queue_depth', 0)
                print(f"  [{i+1:2d}s] Workers: {worker_count} | Queue Depth: {queue_depth}")
            else:
                print(f"  [{i+1:2d}s] Dashboard API error: {data}")
        except Exception as e:
            print(f"  [{i+1:2d}s] Error: {e}")
        
        time.sleep(1)
    
    return test_dashboard_api()

def test_autonomous_scaling():
    """Test the full autonomous workflow"""
    print("\n🚀 Starting Autonomous System Test")
    print("="*50)
    
    # Step 1: Check initial state
    print("\n📊 Step 1: Checking initial system state...")
    success, data = test_dashboard_api()
    if not success:
        print(f"❌ Dashboard not available: {data}")
        return False
    
    initial_workers = data.get('worker_count', 0)
    initial_queue = data.get('queue_depth', 0)
    print(f"  Initial workers: {initial_workers}")
    print(f"  Initial queue depth: {initial_queue}")
    
    # Step 2: Enqueue a task
    print("\n📬 Step 2: Enqueuing test task...")
    test_task = "Create a simple Python function that returns 'Hello, World!'"
    result = test_enqueue_task(test_task)
    print(f"  Enqueue result: {result}")
    
    # Step 3: Check if worker gets spawned
    print("\n🤖 Step 3: Monitoring for worker auto-spawn...")
    success, data = test_dashboard_api()
    if not success:
        print(f"❌ Dashboard error: {data}")
        return False
    
    queue_depth = data.get('queue_depth', 0)
    print(f"  Queue depth after enqueue: {queue_depth}")
    
    # Step 4: Wait for auto-scaling
    print("\n⏳ Step 4: Waiting for auto-scaling (10 seconds)...")
    time.sleep(10)
    
    success, data = test_dashboard_api()
    if success:
        worker_count = data.get('worker_count', 0)
        queue_depth = data.get('queue_depth', 0)
        print(f"  Workers after wait: {worker_count}")
        print(f"  Queue depth after wait: {queue_depth}")
        
        if worker_count > initial_workers:
            print("  ✅ Auto-scaling detected!")
        else:
            print("  ⚠️  No auto-scaling detected")
    
    # Step 5: Manual worker test
    print("\n🔧 Step 5: Testing manual worker spawning...")
    success, worker_id = test_worker_spawning()
    if success:
        print(f"  ✅ Manual worker spawned: {worker_id}")
    else:
        print(f"  ❌ Manual worker spawn failed: {worker_id}")
    
    # Step 6: Monitor for 30 seconds
    print("\n📈 Step 6: 30-second monitoring session...")
    success, final_data = monitor_system()
    
    # Step 7: Cleanup test
    print("\n🧹 Step 7: Testing worker cleanup...")
    try:
        response = requests.get('http://localhost:5001/api/workers', timeout=5)
        if response.status_code == 200:
            workers = response.json().get('workers', [])
            print(f"  Current workers: {len(workers)}")
            for worker in workers:
                print(f"    - {worker['id']} (PID: {worker['pid']}, Status: {worker['status']})")
    except Exception as e:
        print(f"  Error getting workers: {e}")
    
    return True

if __name__ == "__main__":
    # Check if Redis is running
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.ping()
        print("✅ Redis is running")
    except Exception as e:
        print(f"❌ Redis not available: {e}")
        print("  Please start Redis: redis-server")
        sys.exit(1)
    
    # Check if dashboard is running
    success, result = test_dashboard_api()
    if not success:
        print("❌ Dashboard not running at http://localhost:5001")
        print("  Please start the dashboard: python dashboard/app.py --workspace /path/to/workspace")
        sys.exit(1)
    
    print("✅ Dashboard is running")
    
    # Run the test
    test_autonomous_scaling()
    
    print("\n" + "="*50)
    print("🏁 Test completed!")
    print("\nNext steps:")
    print("  1. Open http://localhost:5001 in a browser")
    print("  2. Enqueue more tasks using enqueue.py")
    print("  3. Watch the auto-scaling in action!")
