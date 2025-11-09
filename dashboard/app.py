from flask import Flask, jsonify, render_template, request
import json
import os
import subprocess
import time
import threading
import argparse
import re

app = Flask(__name__)

# Set up argument parser
parser = argparse.ArgumentParser(description="Run the dashboard server.")
parser.add_argument('--workspace', type=str, required=True, help='Path to the workspace directory')
args = parser.parse_args()

# Global workspace path from arguments
WORKSPACE_PATH = args.workspace
TASK_FILE = os.path.join(WORKSPACE_PATH, '.track_task', 'tasks.json')
LOG_FILE = os.path.join(WORKSPACE_PATH, 'manager.log')
TOKEN_LOG_FILE = os.path.join(WORKSPACE_PATH, 'token_usage.log')

# Worker management
worker_processes = {}
monitoring_active = False
monitor_thread = None

# Worker health tracking
worker_health = {}

# Cost tracking
total_cost = 0.0
free_models_used = 0

# Redis connection for queue monitoring
try:
    import redis
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_available = True
except ImportError:
    redis_client = None
    redis_available = False

# Model pricing data
MODEL_PRICING = {
    'minimax/minimax-m2': {'input': 0.255, 'output': 1.02, 'tier': 'Level 4.5'},
    'minimax/minimax-m2:free': {'input': 0, 'output': 0, 'tier': 'Free'},
    'deepseek/deepseek-v3.1': {'input': 0.07, 'output': 1.10, 'tier': 'Orchestration'},
    'moonshotai/kimi-k2-thinking': {'input': 0.40, 'output': 1.20, 'tier': 'Orchestration'},
    'qwen/qwen3-32b:thinking': {'input': 0.29, 'output': 0.59, 'tier': 'Level 4'},
    'free': {'input': 0, 'output': 0, 'tier': 'Free'}
}

# Cost optimization: prioritize free and cheap models
FREE_MODELS = [
    'minimax/minimax-m2:free',
    'deepseek/deepseek-v1:free',
    'deepseek/deepseek-v2:free', 
    'deepseek/deepseek-v3:free',
    'deepseek/deepseek-coder:free',
    'qwen/qwen-14b:free'
]

CHEAP_MODELS = [
    'mistral/mistral-small-3.1',  # $0.06 input / $0.22 output
    'deepseek/deepseek-v3.1-terminus',  # $0.07 input / $1.10 output
    'qwen/qwen3-14b'  # $0.15 input / $0.60 output
]

WORKER_TIMEOUT = 300  # 5 minutes timeout
MAX_WORKERS = 3  # Maximum workers allowed

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    tasks = {}
    if os.path.exists(TASK_FILE):
        try:
            with open(TASK_FILE, 'r') as f:
                tasks = json.load(f)
        except json.JSONDecodeError:
            tasks = {} # File might be empty or being written to
    
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            logs = f.readlines()

    token_usage = 0
    if os.path.exists(TOKEN_LOG_FILE):
        with open(TOKEN_LOG_FILE, 'r') as f:
            try:
                token_usage = int(f.read().strip())
            except ValueError:
                token_usage = 0 # File might be empty

    # Get queue depth if Redis is available
    queue_depth = 0
    if redis_available:
        try:
            queue_depth = redis_client.llen('worker_queue') or 0
        except Exception as e:
            print(f"Redis error: {e}")

    # Get worker details
    workers_info = get_worker_details()

    # Calculate cost summary
    cost_summary = calculate_cost_summary(workers_info)

    return jsonify({
        'tasks': list(tasks.values()),
        'logs': logs[-20:],
        'token_usage': token_usage,
        'worker_count': len(worker_processes),
        'queue_depth': queue_depth,
        'workers': workers_info,
        'cost_summary': cost_summary
    })

def get_worker_details():
    """Get detailed information about all workers"""
    workers = []
    current_time = time.time()
    
    for worker_id, info in worker_processes.items():
        process = info['process']
        status = 'active' if process.poll() is None else 'stopped'
        
        # Get health status
        health = worker_health.get(worker_id, {'status': 'unknown'})
        last_heartbeat = info.get('last_heartbeat', info['start_time'])
        time_since_heartbeat = current_time - last_heartbeat
        
        # Determine if worker is problematic
        is_problematic = (
            status == 'stopped' or 
            health.get('status') == 'timeout' or
            time_since_heartbeat > WORKER_TIMEOUT
        )
        
        # Get process info
        cpu_percent = 0
        memory_usage = 0
        try:
            cpu_percent = process.cpu_percent() if hasattr(process, 'cpu_percent') else 0
            memory_usage = process.memory_info().rss / 1024 / 1024 if hasattr(process, 'memory_info') else 0  # MB
        except:
            pass

        # Get model assignment
        model = assign_worker_model()
        
        # Get pricing info
        pricing = MODEL_PRICING.get(model, MODEL_PRICING['free'])
        is_free = pricing['input'] == 0 and pricing['output'] == 0

        # Calculate approximate cost for this worker
        worker_cost = calculate_worker_cost(info, pricing)

        worker_detail = {
            'id': worker_id,
            'pid': info['pid'],
            'status': status,
            'health_status': health.get('status', 'unknown'),
            'time_since_heartbeat': round(time_since_heartbeat, 0),
            'is_problematic': is_problematic,
            'start_time': info['start_time'],
            'model': model,
            'cpu_percent': round(cpu_percent, 1),
            'memory_mb': round(memory_usage, 1),
            'tier': pricing['tier'],
            'is_free': is_free,
            'cost_spent': worker_cost,
            'tokens_used': calculate_worker_tokens(info),
            'current_task': get_worker_current_task(worker_id),
            'response_time': calculate_response_time(worker_id),
            'message': health.get('message', '')
        }
        workers.append(worker_detail)
    
    return workers

def assign_worker_model():
    """Smart model assignment: FREE first, then CHEAP"""
    # Count current free vs paid models
    free_count = sum(1 for w in worker_processes.values() if is_free_model(w.get('model', '')))
    
    # If no free models assigned yet, use free
    if free_count == 0:
        return FREE_MODELS[len(worker_processes) % len(FREE_MODELS)]
    # If we have some free models, use cheap models
    else:
        return CHEAP_MODELS[len(worker_processes) % len(CHEAP_MODELS)]

def is_free_model(model_id):
    """Check if model is free tier"""
    return any(free_model in model_id for free_model in FREE_MODELS)

def calculate_worker_cost(worker_info, pricing):
    """Calculate approximate cost for a worker based on runtime"""
    runtime = time.time() - worker_info['start_time']
    # Simulate cost based on runtime (in real implementation, track actual API calls)
    minutes = runtime / 60
    estimated_calls = max(1, int(minutes / 2))  # Assume 1 call per 2 minutes
    
    if pricing['input'] == 0:  # Free model
        return 0.0
    
    # Simulate average tokens per call
    avg_input_tokens = 1000
    avg_output_tokens = 500
    
    input_cost = (avg_input_tokens / 1000000) * pricing['input'] * estimated_calls
    output_cost = (avg_output_tokens / 1000000) * pricing['output'] * estimated_calls
    
    return round(input_cost + output_cost, 4)

def calculate_worker_tokens(worker_info):
    """Calculate estimated tokens used by worker"""
    runtime = time.time() - worker_info['start_time']
    # Simulate token usage (in real implementation, track actual usage)
    return int(runtime / 60 * 50)  # ~50 tokens per minute

def get_worker_current_task(worker_id):
    """Get current task being processed by worker"""
    # In a real implementation, this would query the worker
    # For now, simulate based on queue depth
    if redis_available:
        try:
            queue_depth = redis_client.llen('worker_queue')
            if queue_depth > 0:
                return f"Processing task from {queue_depth} in queue"
        except:
            pass
    return "Idle - waiting for tasks"

def calculate_response_time(worker_id):
    """Calculate average response time for worker"""
    # Simulate response time (in real implementation, track actual API response times)
    import random
    return random.randint(500, 3000)  # 0.5-3 seconds

def calculate_cost_summary(workers):
    """Calculate overall cost summary"""
    total_cost = sum(worker['cost_spent'] for worker in workers)
    free_models = sum(1 for worker in workers if worker['is_free'])
    paid_models = len(workers) - free_models
    
    avg_response_time = sum(worker['response_time'] for worker in workers) / max(1, len(workers))
    
    return {
        'total_cost': round(total_cost, 2),
        'free_models': free_models,
        'paid_models': paid_models,
        'average_response_time': round(avg_response_time, 0)
    }

@app.route('/api/spawn-worker', methods=['POST'])
def spawn_worker():
    try:
        # Start a new worker process
        worker_id = f"worker_{int(time.time())}"
        worker_script = os.path.join(os.path.dirname(__file__), '..', 'worker_process.py')
        
        env = os.environ.copy()
        env['ORCHESTRATOR_WORKSPACE'] = WORKSPACE_PATH
        
        process = subprocess.Popen(
            ['python', worker_script],
            cwd=WORKSPACE_PATH,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        worker_processes[worker_id] = {
            'process': process,
            'pid': process.pid,
            'start_time': time.time()
        }
        
        print(f"Spawned worker {worker_id} with PID {process.pid}")
        return jsonify({'success': True, 'worker_id': worker_id, 'pid': process.pid})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stop-worker', methods=['POST'])
def stop_worker():
    data = request.json
    worker_id = data.get('worker_id')
    
    if worker_id not in worker_processes:
        return jsonify({'success': False, 'error': 'Worker not found'}), 404
    
    try:
        worker_info = worker_processes[worker_id]
        process = worker_info['process']
        
        if process.poll() is None:  # Process is still running
            process.terminate()
            process.wait(timeout=5)
        
        del worker_processes[worker_id]
        print(f"Stopped worker {worker_id}")
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stop-worker-revert', methods=['POST'])
def stop_worker_revert():
    """Stop worker and revert its task back to queue"""
    data = request.json
    worker_id = data.get('worker_id')
    
    if worker_id not in worker_processes:
        return jsonify({'success': False, 'error': 'Worker not found'}), 404
    
    try:
        worker_info = worker_processes[worker_id]
        process = worker_info['process']
        
        # Get the task this worker was working on
        current_task = get_worker_current_task(worker_id)
        
        # Stop the worker process
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        
        # Revert task back to queue if we have it
        if current_task and current_task != "Idle - waiting for tasks":
            task_data = json.dumps(current_task)
            redis_client.lpush('worker_queue', task_data)
            print(f"Reverted task back to queue from {worker_id}")
        
        # Clean up
        if worker_id in worker_health:
            del worker_health[worker_id]
        del worker_processes[worker_id]
        
        return jsonify({
            'success': True, 
            'message': f'Worker {worker_id} stopped, task reverted to queue',
            'reverted_task': current_task
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/workers')
def get_workers():
    workers_info = []
    for worker_id, info in worker_processes.items():
        process = info['process']
        status = 'active' if process.poll() is None else 'stopped'
        workers_info.append({
            'id': worker_id,
            'pid': info['pid'],
            'status': status,
            'start_time': info['start_time'],
            'model': assign_worker_model(),
            'tier': MODEL_PRICING.get(assign_worker_model(), {}).get('tier', 'unknown')
        })
    
    return jsonify({'workers': workers_info})

@app.route('/api/worker-health')
def get_worker_health():
    """Get detailed worker health information"""
    health_data = []
    current_time = time.time()
    
    for worker_id, info in worker_processes.items():
        process = info['process']
        status = 'active' if process.poll() is None else 'stopped'
        
        # Get health status
        health = worker_health.get(worker_id, {'status': 'unknown'})
        last_heartbeat = info.get('last_heartbeat', info['start_time'])
        time_since_heartbeat = current_time - last_heartbeat
        
        # Determine if worker is problematic
        is_problematic = (
            status == 'stopped' or 
            health.get('status') == 'timeout' or
            time_since_heartbeat > WORKER_TIMEOUT
        )
        
        health_data.append({
            'id': worker_id,
            'status': status,
            'health_status': health.get('status', 'unknown'),
            'time_since_heartbeat': round(time_since_heartbeat, 0),
            'is_problematic': is_problematic,
            'message': health.get('message', ''),
            'model': assign_worker_model(),
            'cost_spent': calculate_worker_cost(info, MODEL_PRICING.get(assign_worker_model(), {})),
            'last_update': health.get('last_check', 0)
        })
    
    return jsonify({'workers_health': health_data})

@app.route('/api/parse-prd', methods=['POST'])
def parse_prd():
    try:
        data = request.json
        prd_text = data.get('prd', '')
        project_name = data.get('project_name', '').strip()
        
        if not prd_text.strip():
            return jsonify({'success': False, 'error': 'PRD text is empty'}), 400
        
        # Create a new project folder for the parsed PRD
        if project_name:
            # Clean project name
            project_name = re.sub(r'[^\w\-_]', '_', project_name)
            project_name = project_name.lower()
            
            # Get the workspaces directory
            base_dir = os.path.dirname(os.path.dirname(WORKSPACE_PATH))  # Go up to Agentic_Work_Shop
            workspaces_dir = os.path.join(base_dir, 'dev_workspaces')
            new_project_path = os.path.join(workspaces_dir, project_name)
            
            # Create the project directory
            if not os.path.exists(workspaces_dir):
                os.makedirs(workspaces_dir)
                
            if os.path.exists(new_project_path):
                return jsonify({'success': False, 'error': f'Project "{project_name}" already exists'}), 400
            
            os.makedirs(new_project_path)
            
            # Create basic project structure
            create_basic_project_structure(new_project_path, project_name)
            
            # Add project_path to each task
            tasks = parse_prd_to_tasks(prd_text, new_project_path)
            for task in tasks:
                task['project_path'] = new_project_path
                task['project_name'] = project_name
            
            return jsonify({
                'success': True,
                'tasks': tasks,
                'count': len(tasks),
                'project_created': True,
                'project_path': new_project_path,
                'project_name': project_name
            })
        else:
            # Parse without creating a project folder
            tasks = parse_prd_to_tasks(prd_text, None)
            return jsonify({
                'success': True,
                'tasks': tasks,
                'count': len(tasks),
                'project_created': False
            })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/enqueue-task', methods=['POST'])
def enqueue_task():
    try:
        if not redis_available:
            return jsonify({'success': False, 'error': 'Redis not available'}), 500
        
        task_data = request.json
        
        # Validate required fields
        required_fields = ['branch', 'instruction', 'goal', 'target_paths']
        for field in required_fields:
            if field not in task_data:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
        
        # Enqueue to Redis
        task_json = json.dumps(task_data)
        redis_client.lpush('worker_queue', task_json)
        
        # Log the enqueue for tracking
        print(f"Enqueued task: {task_data.get('instruction', 'Unknown')[:50]}...")
        
        return jsonify({
            'success': True,
            'message': 'Task enqueued successfully',
            'queue_depth': redis_client.llen('worker_queue')
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def parse_prd_to_tasks(prd_text, project_path=None):
    """Parse PRD text into actionable tasks"""
    tasks = []
    
    # Split by lines and clean up
    lines = [line.strip() for line in prd_text.split('\n') if line.strip()]
    
    task_counter = 1
    for line in lines:
        # Skip headers and empty lines
        if not line or line.startswith('#') or line.lower() in ['overview', 'summary', 'introduction']:
            continue
        
        # Remove common prefixes
        clean_line = re.sub(r'^[\s\-*•\d\.\)]+\s*', '', line).strip()
        
        # Skip very short lines
        if len(clean_line) < 10:
            continue
        
        # Extract action words and convert to tasks
        action_patterns = {
            r'create\s+(.+)': 'Create',
            r'build\s+(.+)': 'Build',
            r'implement\s+(.+)': 'Implement',
            r'add\s+(.+)': 'Add',
            r'develop\s+(.+)': 'Develop',
            r'design\s+(.+)': 'Design',
            r'configure\s+(.+)': 'Configure',
            r'setup\s+(.+)': 'Setup',
            r'integrate\s+(.+)': 'Integrate',
            r'modify\s+(.+)': 'Modify',
            r'update\s+(.+)': 'Update',
            r'fix\s+(.+)': 'Fix',
            r'refactor\s+(.+)': 'Refactor',
            r'test\s+(.+)': 'Test',
            r'verify\s+(.+)': 'Verify'
        }
        
        task_title = None
        task_instruction = None
        
        for pattern, action in action_patterns.items():
            match = re.search(pattern, clean_line, re.IGNORECASE)
            if match:
                target = match.group(1).strip()
                # Clean up the target
                target = re.sub(r'\s+', '_', target).lower()
                target = re.sub(r'[^\w\-_]', '', target)
                
                task_title = f"{action} {target.replace('_', ' ').title()}"
                task_instruction = f"{action} {target.replace('_', ' ')}"
                break
        
        if not task_title:
            # Fallback: use the line as-is
            task_title = clean_line[:50] + "..." if len(clean_line) > 50 else clean_line
            task_instruction = clean_line
        
        # Generate branch name
        branch_name = f"feature/task-{task_counter:02d}-{task_title.lower().replace(' ', '-')[:20]}"
        
        # Determine priority based on task type
        priority = determine_priority(clean_line)
        
        # Determine target file based on task type
        target_files = determine_target_files(clean_line, task_instruction)
        
        task = {
            'title': task_title,
            'branch': branch_name,
            'instruction': task_instruction,
            'goal': f"Implement: {task_instruction}",
            'target_paths': target_files,
            'priority': priority
        }
        
        tasks.append(task)
        task_counter += 1
    
    return tasks

def determine_priority(instruction):
    """Determine task priority based on instruction content"""
    instruction_lower = instruction.lower()
    
    high_priority_keywords = ['auth', 'security', 'critical', 'login', 'password', 'payment', 'database']
    low_priority_keywords = ['cosmetic', 'ui', 'style', 'color', 'font', 'documentation', 'readme']
    
    if any(keyword in instruction_lower for keyword in high_priority_keywords):
        return 'high'
    elif any(keyword in instruction_lower for keyword in low_priority_keywords):
        return 'low'
    else:
        return 'medium'

def determine_target_files(instruction, clean_line):
    """Determine appropriate target files based on instruction"""
    instruction_lower = instruction.lower()
    clean_lower = clean_line.lower()
    
    # File type patterns (using the standard project structure we create)
    if any(keyword in instruction_lower for keyword in ['api', 'endpoint', 'server', 'backend']):
        return ['src/api/main.py', 'src/api/routes.py']
    elif any(keyword in instruction_lower for keyword in ['ui', 'interface', 'component', 'frontend', 'web']):
        return ['src/frontend/index.html', 'src/frontend/style.css', 'src/frontend/script.js']
    elif any(keyword in instruction_lower for keyword in ['database', 'model', 'schema', 'sql']):
        return ['src/database/models.py', 'src/database/migrations.py']
    elif any(keyword in instruction_lower for keyword in ['test', 'testing', 'spec']):
        return ['tests/test_implementation.py', 'tests/conftest.py']
    elif any(keyword in instruction_lower for keyword in ['config', 'configuration', 'settings']):
        return ['config/settings.py', 'config/environment.py']
    elif any(keyword in instruction_lower for keyword in ['auth', 'login', 'user', 'authentication']):
        return ['src/auth/user.py', 'src/auth/middleware.py', 'src/auth/routes.py']
    elif any(keyword in instruction_lower for keyword in ['docker', 'deploy', 'deployment']):
        return ['Dockerfile', 'docker-compose.yml', 'deploy.sh']
    else:
        return ['src/main.py', 'src/utils.py']

def create_basic_project_structure(project_path, project_name):
    """Create basic project structure for new projects"""
    # Create basic directories
    directories = [
        'src',
        'tests', 
        'docs',
        'config'
    ]
    
    for directory in directories:
        dir_path = os.path.join(project_path, directory)
        os.makedirs(dir_path, exist_ok=True)
    
    # Create basic files
    files_to_create = {
        'README.md': f"""# {project_name.replace('_', ' ').title()}

## Project Overview
This project was generated from a PRD and contains the basic structure for development.

## Directory Structure
- `src/` - Source code
- `tests/` - Test files  
- `docs/` - Documentation
- `config/` - Configuration files

## Getting Started
TODO: Add setup instructions

""",
        'requirements.txt': """# Add your dependencies here
""",
        '.gitignore': """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Node.js (if needed)
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# Environment
.env
.env.local
.env.development.local
.env.test.local
.env.production.local

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db
""",
        'src/main.py': f'"""Main module for {project_name}"""\n\n\ndef main():\n    """Main entry point"""  \n    pass\n\n\nif __name__ == "__main__":\n    main()\n',
        'tests/test_main.py': f'"""Tests for {project_name}"""\n\ndef test_main():\n    """Test main functionality"""  \n    assert True\n'
    }
    
    for file_path, content in files_to_create.items():
        full_path = os.path.join(project_path, file_path)
        with open(full_path, 'w') as f:
            f.write(content)
    
    # Make main.py executable on Unix systems
    main_py_path = os.path.join(project_path, 'src', 'main.py')
    if os.name == 'posix':
        os.chmod(main_py_path, 0o755)
    
    print(f"Created project structure for: {project_name} at {project_path}")

def check_worker_health():
    """Check worker health and mark unhealthy workers"""
    current_time = time.time()
    for worker_id, info in list(worker_processes.items()):
        process = info['process']
        last_heartbeat = info.get('last_heartbeat', info['start_time'])
        
        # Check if worker is alive
        if process.poll() is not None:
            # Worker died, mark as unhealthy
            if worker_id not in worker_health:
                worker_health[worker_id] = {'status': 'stopped', 'last_check': current_time}
            continue
        
        # Check if worker is responsive
        time_since_heartbeat = current_time - last_heartbeat
        if time_since_heartbeat > WORKER_TIMEOUT:
            # Worker not responding
            worker_health[worker_id] = {
                'status': 'timeout', 
                'last_check': current_time,
                'message': f'No response for {int(time_since_heartbeat)}s'
            }
        else:
            # Worker is healthy
            worker_health[worker_id] = {
                'status': 'healthy',
                'last_check': current_time
            }

def monitor_queues():
    """Monitor Redis queues with strict cost controls"""
    global monitoring_active
    
    while monitoring_active:
        try:
            if not redis_available:
                time.sleep(5)
                continue
            
            # Check queue depth
            queue_depth = redis_client.llen('worker_queue') or 0
            active_workers = len([w for w in worker_processes.values() if w['process'].poll() is None])
            
            # STRICT COST CONTROL: Only spawn workers if conditions are met
            if queue_depth > 0 and active_workers == 0 and len(worker_processes) < MAX_WORKERS:
                print(f"Queue has {queue_depth} tasks, spawning cost-controlled worker...")
                spawn_worker()
            elif queue_depth > 2 and active_workers < 2 and len(worker_processes) < MAX_WORKERS:
                print(f"High queue depth ({queue_depth}), adding 2nd worker...")
                spawn_worker()
            # NO MORE AUTO-SCALING beyond 3 workers!
            
            check_worker_health()
            time.sleep(30)  # Check every 30 seconds (reduced frequency)
            
        except Exception as e:
            print(f"Monitor error: {e}")
            time.sleep(30)

def start_monitoring():
    global monitoring_active, monitor_thread
    if not monitoring_active:
        monitoring_active = True
        monitor_thread = threading.Thread(target=monitor_queues, daemon=True)
        monitor_thread.start()
        print("Queue monitoring started")

def stop_monitoring():
    global monitoring_active
    monitoring_active = False
    print("Queue monitoring stopped")

# EMERGENCY: DISABLE AUTO-SCALING TO PREVENT MONEY WASTE
monitoring_active = False
# max_workers = 0  # TEMPORARILY DISABLE ALL WORKER SPAWNING
# auto_scaling_enabled = False

# Start monitoring when the app starts
start_monitoring()

if __name__ == '__main__':
    app.run(port=5001, debug=True)
