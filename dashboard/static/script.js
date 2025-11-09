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
    const projectNameInput = document.getElementById('project-name-input');

    // Add emergency stop button
    const emergencyStopBtn = document.getElementById('emergency-stop-btn');

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
                
                // Update active worker count display
                document.getElementById('active-workers-count').textContent = data.worker_count || 0;
                
                // Update add worker button state based on current limit
                updateAddWorkerButtonState();
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
            
            // Add problematic indicator
            if (worker.is_problematic) {
                card.classList.add('worker-problematic');
            }
            
            const status = worker.status || 'unknown';
            const modelInfo = getWorkerModelInfo(worker.model);
            const costInfo = calculateWorkerCost(worker);
            
            // Format response time
            const responseTime = formatResponseTime(worker.time_since_heartbeat * 1000);
            
            card.innerHTML = `
                <div class="worker-header">
                    <div class="worker-id">${worker.id}</div>
                    <div class="worker-status ${status} ${worker.is_problematic ? 'problematic' : ''}">
                        ${worker.is_problematic ? '⚠️' : '✅'} ${status}
                    </div>
                </div>
                
                <div class="worker-model">${modelInfo.name}</div>
                <div class="worker-cost">${modelInfo.cost} ${modelInfo.tier}</div>
                
                <div class="worker-task">
                    ${worker.current_task || 'Idle - waiting for tasks'}
                </div>
                
                <div class="worker-health">
                    <span class="health-status ${worker.health_status}">
                        Health: ${worker.health_status}
                    </span>
                    <span class="response-time">
                        Last: ${responseTime}
                    </span>
                </div>
                
                <div class="task-stats">
                    <span>Uptime: ${formatUptime(worker.start_time)}</span>
                    <span>${worker.tokens_used || 0} tokens</span>
                </div>
                
                <div class="worker-actions">
                    <button class="btn-stop-worker" onclick="stopWorkerAndRevert('${worker.id}')">
                        🛑 Stop & Revert Task
                    </button>
                </div>
                
                ${worker.message ? `<div class="worker-message">${worker.message}</div>` : ''}
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
        // Get current worker limit from slider
        const workerLimit = document.getElementById('worker-limit-slider').value;
        
        // Check current worker count
        fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                const currentWorkers = data.worker_count || 0;
                const maxWorkers = parseInt(workerLimit);
                
                if (currentWorkers >= maxWorkers) {
                    alert(`⚠️ Cost control: Maximum ${maxWorkers} workers allowed to prevent money waste. Current: ${currentWorkers}`);
                    return;
                }
                
                // Proceed with worker spawning
                addWorkerBtn.disabled = true;
                addWorkerBtn.textContent = '⏳ Starting (Cost Controlled)...';
                
                fetch('/api/spawn-worker', { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        addWorkerBtn.disabled = false;
                        addWorkerBtn.textContent = '➕ Add Worker (Cost Controlled)';
                        
                        if (data.success) {
                            console.log('Cost-controlled worker spawned:', data);
                            showAutoScalingIndicator();
                            updateStatus();
                        } else {
                            alert('Failed to spawn worker: ' + data.error);
                        }
                    })
                    .catch(error => {
                        addWorkerBtn.disabled = false;
                        addWorkerBtn.textContent = '➕ Add Worker (Cost Controlled)';
                        console.error('Error:', error);
                        alert('Error spawning worker');
                    });
            });
    }

    // Worker limit slider functionality
    function updateWorkerLimit(limit) {
        document.getElementById('current-limit').textContent = limit;
        document.getElementById('active-workers-count').textContent = getActiveWorkerCount();
        
        // Save to localStorage
        localStorage.setItem('workerLimit', limit);
        
        // Apply the limit
        applyWorkerLimit(parseInt(limit));
        
        // Update add worker button state
        updateAddWorkerButtonState();
    }
    
    function getActiveWorkerCount() {
        const workerGrid = document.getElementById('worker-grid');
        if (!workerGrid) return 0;
        return workerGrid.querySelectorAll('.worker-card:not(.inactive)').length;
    }
    
    function applyWorkerLimit(limit) {
        // Check if current workers exceed new limit
        const currentWorkers = getActiveWorkerCount();
        if (currentWorkers > limit) {
            const excess = currentWorkers - limit;
            console.log(`⚠️ Worker limit reduced. ${excess} workers exceeding new limit.`);
            
            // Show notification (could be enhanced with a proper toast system)
            const notification = document.createElement('div');
            notification.className = 'limit-exceeded-notification';
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                background: #ffc107;
                color: #000;
                padding: 15px;
                border-radius: 8px;
                z-index: 1000;
                box-shadow: 0 4px 8px rgba(0,0,0,0.3);
            `;
            notification.textContent = `⚠️ Worker limit set to ${limit}. Currently running ${currentWorkers} workers. Consider removing excess workers.`;
            document.body.appendChild(notification);
            
            setTimeout(() => {
                document.body.removeChild(notification);
            }, 5000);
        }
    }
    
    function updateAddWorkerButtonState() {
        const currentWorkers = getActiveWorkerCount();
        const limit = document.getElementById('worker-limit-slider').value;
        
        if (currentWorkers >= parseInt(limit)) {
            addWorkerBtn.disabled = true;
            addWorkerBtn.textContent = '➕ Add Worker (Limit Reached)';
        } else {
            addWorkerBtn.disabled = false;
            addWorkerBtn.textContent = '➕ Add Worker (Cost Controlled)';
        }
    }
    
    // Load saved worker limit
    function loadWorkerLimit() {
        const savedLimit = localStorage.getItem('workerLimit');
        if (savedLimit) {
            const slider = document.getElementById('worker-limit-slider');
            slider.value = savedLimit;
            updateWorkerLimit(savedLimit);
        }
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

    function emergencyStopAll() {
        if (!confirm('🚨 EMERGENCY: Stop all workers to prevent cost waste? This will stop ALL active workers.')) {
            return;
        }
        
        emergencyStopBtn.disabled = true;
        emergencyStopBtn.textContent = '⏳ Stopping All...';
        
        fetch('/api/workers')
            .then(response => response.json())
            .then(data => {
                let stoppedCount = 0;
                const totalWorkers = data.workers.length;
                
                data.workers.forEach(worker => {
                    fetch('/api/stop-worker', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ worker_id: worker.id })
                    })
                    .then(response => response.json())
                    .then(result => {
                        stoppedCount++;
                        if (stoppedCount === totalWorkers) {
                            emergencyStopBtn.disabled = false;
                            emergencyStopBtn.textContent = '🛑 Emergency Stop All';
                            alert(`🚨 Emergency stop complete! Stopped ${totalWorkers} workers.`);
                            updateStatus();
                        }
                    });
                });
            })
            .catch(error => {
                emergencyStopBtn.disabled = false;
                emergencyStopBtn.textContent = '🛑 Emergency Stop All';
                console.error('Error in emergency stop:', error);
            });
    }

    function parsePRD() {
        const prdText = prdInput.value.trim();
        const projectName = projectNameInput.value.trim();
        
        if (!prdText) {
            showPRDMessage('Please paste a PRD first.', 'error');
            return;
        }

        parsePrdBtn.disabled = true;
        parsePrdBtn.textContent = '⏳ Parsing...';
        
        const requestData = { prd: prdText };
        if (projectName) {
            requestData.project_name = projectName;
        }
        
        fetch('/api/parse-prd', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        })
        .then(response => response.json())
        .then(data => {
            parsePrdBtn.disabled = false;
            parsePrdBtn.textContent = '🔍 Parse PRD & Create Project';
            
            if (data.success) {
                displayParsedTasks(data.tasks, data);
                
                let message = `Successfully parsed ${data.tasks.length} tasks!`;
                if (data.project_created) {
                    message += ` Project "${data.project_name}" created in dev_workspaces/`;
                }
                showPRDMessage(message, 'success');
            } else {
                showPRDMessage('Failed to parse PRD: ' + data.error, 'error');
            }
        })
        .catch(error => {
            parsePrdBtn.disabled = false;
            parsePrdBtn.textContent = '🔍 Parse PRD & Create Project';
            showPRDMessage('Error parsing PRD: ' + error.message, 'error');
        });
    }

    function displayParsedTasks(tasks, projectData = null) {
        prdResults.innerHTML = '';
        
        if (tasks.length === 0) {
            prdResults.innerHTML = '<p>No tasks could be extracted from the PRD.</p>';
            return;
        }

        // Show project creation info if available
        if (projectData && projectData.project_created) {
            const projectInfo = document.createElement('div');
            projectInfo.className = 'project-info';
            projectInfo.innerHTML = `
                <div class="project-success">
                    <h3>📁 Project Created Successfully!</h3>
                    <p><strong>Name:</strong> ${projectData.project_name}</p>
                    <p><strong>Path:</strong> ${projectData.project_path}</p>
                    <p><strong>Status:</strong> Ready for development</p>
                </div>
            `;
            prdResults.appendChild(projectInfo);
        }

        tasks.forEach((task, index) => {
            const taskDiv = document.createElement('div');
            taskDiv.className = 'prd-task';
            
            // Add project path to task display if available
            const taskPathInfo = projectData && projectData.project_created ? 
                `<small>Project: ${projectData.project_name}</small>` : '';
            
            taskDiv.innerHTML = `
                <h4>Task ${index + 1}: ${task.title}</h4>
                <p>${task.instruction}</p>
                ${taskPathInfo}
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
                const currentLimit = document.getElementById('worker-limit-slider').value;
                
                if (queueDepth > 0 && workerCount === 0) {
                    showAutoScalingIndicator();
                } else if (queueDepth > 5 && workerCount < parseInt(currentLimit)) {
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
    emergencyStopBtn.addEventListener('click', emergencyStopAll);
    
    // Worker limit slider
    const workerLimitSlider = document.getElementById('worker-limit-slider');
    if (workerLimitSlider) {
        workerLimitSlider.addEventListener('input', (e) => {
            updateWorkerLimit(e.target.value);
        });
    }

    // Auto-scaling indicator
    setInterval(updateScalingIndicator, 10000);

    // Initial load and periodic refresh
    loadWorkerLimit();
    updateStatus();
    setInterval(() => {
        updateStatus();
        updateAddWorkerButtonState();
    }, 5000); // Refresh every 5 seconds
});
