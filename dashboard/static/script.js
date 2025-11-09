document.addEventListener('DOMContentLoaded', function() {
    const taskCountEl = document.getElementById('task-count');
    const tokenUsageEl = document.getElementById('token-usage');
    const workerCountEl = document.getElementById('worker-count');
    const queueDepthEl = document.getElementById('queue-depth');
    const addWorkerBtn = document.getElementById('add-worker-btn');
    const removeWorkerBtn = document.getElementById('remove-worker-btn');
    const refreshBtn = document.getElementById('refresh-btn');
    const taskGridEl = document.getElementById('task-grid');
    const liveLogEl = document.getElementById('live-log');
    const workerGridEl = document.getElementById('worker-grid');
    
    // Cost tracking elements
    const totalCostEl = document.getElementById('total-cost');
    const freeUsageEl = document.getElementById('free-usage');
    const avgResponseEl = document.getElementById('avg-response');

    // PRD Parser elements
    const prdInput = document.getElementById('prd-input');
    const parsePrdBtn = document.getElementById('parse-prd-btn');
    const clearPrdBtn = document.getElementById('clear-prd-btn');
    const prdResults = document.getElementById('prd-results');

    // Track worker data
    let workerData = [];
    let totalCost = 0;
    let freeTierCount = 0;
    let responseTimes = [];

    function calculateCost(tokens, modelType) {
        // Cost calculation based on OpenRouter pricing (per 1M tokens)
        if (modelType === 'free') {
            return 0;
        }
        
        const costs = {
            'minimax/minimax-m2': { input: 0.255, output: 1.02 },
            'deepseek/deepseek-v3.1': { input: 0.07, output: 1.10 },
            'moonshotai/kimi-k2-thinking': { input: 0.40, output: 1.20 },
            'qwen/qwen3-32b:thinking': { input: 0.29, output: 0.59 },
            'default': { input: 0.15, output: 0.60 }
        };
        
        const cost = costs[modelType] || costs['default'];
        const inputCost = (tokens.input_tokens / 1000000) * cost.input;
        const outputCost = (tokens.output_tokens / 1000000) * cost.output;
        return inputCost + outputCost;
    }

    function formatCurrency(amount) {
        return amount < 0.01 ? '<$0.01' : `$${amount.toFixed(2)}`;
    }

    function formatResponseTime(ms) {
        if (ms < 1000) return `${Math.round(ms)}ms`;
        if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
        return `${(ms / 60000).toFixed(1)}m`;
    }

    function updateStatus() {
        fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                // Update basic stats
                taskCountEl.textContent = data.tasks.length;
                tokenUsageEl.textContent = data.token_usage.toLocaleString();
                workerCountEl.textContent = data.worker_count || 0;
                queueDepthEl.textContent = data.queue_depth || 0;

                // Update cost tracking
                if (data.cost_summary) {
                    totalCost = data.cost_summary.total_cost || 0;
                    freeTierCount = data.cost_summary.free_models || 0;
                    totalCostEl.innerHTML = formatCurrency(totalCost);
                    
                    const freePercentage = data.worker_count > 0 
                        ? Math.round((freeTierCount / data.worker_count) * 100) 
                        : 0;
                    freeUsageEl.textContent = `${freePercentage}%`;
                }

                // Update tasks with progress bars
                updateTaskGrid(data.tasks, data.queue_depth);
                
                // Update worker information
                updateWorkerGrid(data.workers || []);
                
                // Update logs
                if (data.logs && data.logs.length > 0) {
                    liveLogEl.innerHTML = data.logs.map(log => 
                        `<div class="log-line">${log}</div>`
                    ).join('');
                    liveLogEl.scrollTop = liveLogEl.scrollHeight;
                }

                // Update worker buttons state
                removeWorkerBtn.disabled = (data.worker_count || 0) === 0;
            })
            .catch(error => console.error('Error fetching status:', error));
    }

    function updateTaskGrid(tasks, queueDepth) {
        taskGridEl.innerHTML = '';
        
        if (tasks.length === 0) {
            const card = document.createElement('div');
            card.className = 'task-card';
            card.innerHTML = '<p>No tasks in queue</p>';
            taskGridEl.appendChild(card);
            return;
        }

        tasks.forEach(task => {
            const card = document.createElement('div');
            card.className = `task-card ${task.status.replace(' ', '_').toLowerCase()}`;
            
            // Calculate progress based on status
            const progress = getTaskProgress(task.status);
            const progressClass = getTaskProgressClass(task.status);
            
            card.innerHTML = `
                <h3>${task.title}</h3>
                <p><strong>Status:</strong> ${task.status}</p>
                <p><strong>ID:</strong> ${task.id}</p>
                <p><strong>Queue Position:</strong> ${getQueuePosition(task.id, queueDepth)}</p>
                
                <div class="progress-container">
                    <div class="progress-bar">
                        <div class="progress-fill ${progressClass}" style="width: ${progress}%"></div>
                    </div>
                </div>
                
                <div class="task-stats">
                    <span>Progress: ${progress}%</span>
                    <span class="task-priority ${task.priority || 'medium'}">${task.priority || 'medium'}</span>
                </div>
                
                ${task.dependencies && task.dependencies.length > 0 ? 
                    `<p><strong>Deps:</strong> ${task.dependencies.join(', ')}</p>` : ''}
            `;
            taskGridEl.appendChild(card);
        });
    }

    function updateWorkerGrid(workers) {
        workerGridEl.innerHTML = '';
        
        if (workers.length === 0) {
            const card = document.createElement('div');
            card.className = 'worker-card';
            card.innerHTML = '<p>No active workers</p>';
            workerGridEl.appendChild(card);
            return;
        }

        workers.forEach(worker => {
            const card = document.createElement('div');
            card.className = 'worker-card';
            
            const status = worker.status || 'unknown';
            const modelInfo = getWorkerModelInfo(worker.model);
            const costInfo = calculateWorkerCost(worker);
            
            card.innerHTML = `
                <div class="worker-header">
                    <div class="worker-id">${worker.id}</div>
                    <div class="worker-status ${status}">${status}</div>
                </div>
                
                <div class="worker-model">${modelInfo.name}</div>
                <div class="worker-cost">${modelInfo.cost} ${modelInfo.tier}</div>
                
                <div class="worker-task">
                    ${worker.current_task || 'Idle - waiting for tasks'}
                </div>
                
                <div class="task-stats">
                    <span>Uptime: ${formatUptime(worker.start_time)}</span>
                    <span>${worker.tokens_used || 0} tokens</span>
                </div>
            `;
            workerGridEl.appendChild(card);
        });
    }

    function getTaskProgress(status) {
        const statusLower = status.toLowerCase();
        if (statusLower.includes('completed') || statusLower.includes('success')) return 100;
        if (statusLower.includes('progress')) return 60;
        if (statusLower.includes('processing')) return 40;
        if (statusLower.includes('pending')) return 20;
        if (statusLower.includes('failed') || statusLower.includes('error')) return 0;
        return 10;
    }

    function getTaskProgressClass(status) {
        const statusLower = status.toLowerCase();
        if (statusLower.includes('completed') || statusLower.includes('success')) return 'completed';
        if (statusLower.includes('processing') || statusLower.includes('progress')) return 'processing';
        if (statusLower.includes('pending')) return 'pending';
        return '';
    }

    function getQueuePosition(taskId, queueDepth) {
        // This would need to be implemented in the backend
        return `Position in ${queueDepth} tasks`;
    }

    function getWorkerModelInfo(modelId) {
        if (!modelId) return { name: 'Unknown', cost: '$0.00', tier: 'free' };
        
        const models = {
            'minimax/minimax-m2': { name: 'MiniMax M2', cost: '$0.26-1.02', tier: 'Level 4.5' },
            'deepseek/deepseek-v3.1': { name: 'DeepSeek V3.1', cost: '$0.07-1.10', tier: 'Orchestration' },
            'moonshotai/kimi-k2-thinking': { name: 'Kimi K2', cost: '$0.40-1.20', tier: 'Orchestration' },
            'qwen/qwen3-32b:thinking': { name: 'Qwen 3 32B', cost: '$0.29-0.59', tier: 'Level 4' },
            'free': { name: 'Free Tier', cost: '$0.00', tier: 'Free' }
        };
        
        if (modelId.includes(':free')) {
            return { name: 'Free Model', cost: '$0.00', tier: 'free' };
        }
        
        return models[modelId] || { name: modelId.split('/').pop(), cost: 'Variable', tier: 'paid' };
    }

    function calculateWorkerCost(worker) {
        // This would calculate cost based on worker's usage
        return `$${(Math.random() * 0.50).toFixed(2)} spent`;
    }

    function formatUptime(startTime) {
        if (!startTime) return 'Unknown';
        const uptime = Date.now() / 1000 - startTime;
        if (uptime < 60) return `${Math.round(uptime)}s`;
        if (uptime < 3600) return `${Math.round(uptime / 60)}m`;
        return `${Math.round(uptime / 3600)}h`;
    }

    function addWorker() {
        addWorkerBtn.disabled = true;
        addWorkerBtn.textContent = '⏳ Starting...';
        
        fetch('/api/spawn-worker', { method: 'POST' })
            .then(response => response.json())
            .then(data => {
                addWorkerBtn.disabled = false;
                addWorkerBtn.textContent = '➕ Add Worker';
                
                if (data.success) {
                    console.log('Worker spawned:', data);
                    showAutoScalingIndicator();
                    updateStatus();
                } else {
                    alert('Failed to spawn worker: ' + data.error);
                }
            })
            .catch(error => {
                addWorkerBtn.disabled = false;
                addWorkerBtn.textContent = '➕ Add Worker';
                console.error('Error:', error);
                alert('Error spawning worker');
            });
    }

    function removeWorker() {
        fetch('/api/workers')
            .then(response => response.json())
            .then(data => {
                if (data.workers.length > 0) {
                    const lastWorker = data.workers[data.workers.length - 1];
                    fetch('/api/stop-worker', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ worker_id: lastWorker.id })
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            console.log('Worker stopped:', lastWorker.id);
                            updateStatus();
                        } else {
                            alert('Failed to stop worker: ' + data.error);
                        }
                    });
                } else {
                    alert('No workers to stop');
                }
            });
    }

    function parsePRD() {
        const prdText = prdInput.value.trim();
        if (!prdText) {
            showPRDMessage('Please paste a PRD first.', 'error');
            return;
        }

        parsePrdBtn.disabled = true;
        parsePrdBtn.textContent = '⏳ Parsing...';
        
        fetch('/api/parse-prd', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prd: prdText })
        })
        .then(response => response.json())
        .then(data => {
            parsePrdBtn.disabled = false;
            parsePrdBtn.textContent = '🔍 Parse PRD';
            
            if (data.success) {
                displayParsedTasks(data.tasks);
                showPRDMessage(`Successfully parsed ${data.tasks.length} tasks!`, 'success');
            } else {
                showPRDMessage('Failed to parse PRD: ' + data.error, 'error');
            }
        })
        .catch(error => {
            parsePrdBtn.disabled = false;
            parsePrdBtn.textContent = '🔍 Parse PRD';
            showPRDMessage('Error parsing PRD: ' + error.message, 'error');
        });
    }

    function displayParsedTasks(tasks) {
        prdResults.innerHTML = '';
        
        if (tasks.length === 0) {
            prdResults.innerHTML = '<p>No tasks could be extracted from the PRD.</p>';
            return;
        }

        tasks.forEach((task, index) => {
            const taskDiv = document.createElement('div');
            taskDiv.className = 'prd-task';
            taskDiv.innerHTML = `
                <h4>Task ${index + 1}: ${task.title}</h4>
                <p>${task.instruction}</p>
                <small>Target: ${task.target_paths.join(', ')}</small>
                <div class="task-stats">
                    <span class="task-priority ${task.priority}">${task.priority}</span>
                    <span>Branch: ${task.branch}</span>
                </div>
            `;
            prdResults.appendChild(taskDiv);
        });

        // Add enqueue all button
        const enqueueAllBtn = document.createElement('button');
        enqueueAllBtn.className = 'btn-primary';
        enqueueAllBtn.textContent = '🚀 Enqueue All Tasks';
        enqueueAllBtn.onclick = () => enqueueAllTasks(tasks);
        prdResults.appendChild(enqueueAllBtn);
    }

    function enqueueAllTasks(tasks) {
        if (tasks.length === 0) {
            showPRDMessage('No tasks to enqueue.', 'error');
            return;
        }

        let enqueuedCount = 0;
        tasks.forEach(task => {
            fetch('/api/enqueue-task', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(task)
            })
            .then(response => response.json())
            .then(data => {
                enqueuedCount++;
                if (enqueuedCount === tasks.length) {
                    showPRDMessage(`Successfully enqueued all ${tasks.length} tasks!`, 'success');
                    updateStatus(); // Refresh the dashboard
                    showAutoScalingIndicator();
                }
            })
            .catch(error => {
                console.error('Error enqueuing task:', error);
            });
        });
    }

    function clearPRD() {
        prdInput.value = '';
        prdResults.innerHTML = '';
    }

    function showPRDMessage(message, type) {
        const messageDiv = document.createElement('div');
        messageDiv.className = type === 'error' ? 'error-message' : 'success-message';
        messageDiv.textContent = message;
        prdResults.insertBefore(messageDiv, prdResults.firstChild);
        
        setTimeout(() => {
            if (messageDiv.parentNode) {
                messageDiv.parentNode.removeChild(messageDiv);
            }
        }, 5000);
    }

    function showAutoScalingIndicator() {
        const indicator = document.getElementById('auto-scaling-indicator');
        indicator.classList.add('scaling');
        setTimeout(() => {
            indicator.classList.remove('scaling');
        }, 3000);
    }

    function updateScalingIndicator() {
        fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                const queueDepth = data.queue_depth || 0;
                const workerCount = data.worker_count || 0;
                
                if (queueDepth > 0 && workerCount === 0) {
                    showAutoScalingIndicator();
                } else if (queueDepth > 5 && workerCount < 3) {
                    showAutoScalingIndicator();
                }
            });
    }

    // Event listeners
    addWorkerBtn.addEventListener('click', addWorker);
    removeWorkerBtn.addEventListener('click', removeWorker);
    refreshBtn.addEventListener('click', updateStatus);
    parsePrdBtn.addEventListener('click', parsePRD);
    clearPrdBtn.addEventListener('click', clearPRD);

    // Auto-scaling indicator
    setInterval(updateScalingIndicator, 10000);

    // Initial load and periodic refresh
    updateStatus();
    setInterval(updateStatus, 5000); // Refresh every 5 seconds
});
