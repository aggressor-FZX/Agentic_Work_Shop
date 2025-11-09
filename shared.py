from __future__ import annotations

import os
import time
import json
from typing import List, Literal, TypedDict, Optional
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'), override=True)

OPENROUTER_BASE = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("OPENAI_API_KEY")

# Load model pricing data
with open(os.path.join(os.path.dirname(__file__), 'openrouter_models.json'), 'r') as f:
    MODEL_DATA = json.load(f)

FREE_TIER_MODELS = [m['id'] for m in MODEL_DATA['open_router_models']['free_tier_models']]

PM_MODELS = [m['id'] for m in MODEL_DATA['open_router_models']['agentic_reasoning_orchestration']]

# Extract cheap worker models
cheap_obedient = MODEL_DATA['open_router_models']['cheap_obedient_models']
FREE_WORKER_MODELS = [m['id'] for m in cheap_obedient['level_1_easy_tasks']] + \
                     [m['id'] for m in cheap_obedient['level_2_medium_tasks']]

PAID_WORKER_MODELS = [m['id'] for m in cheap_obedient['level_3_harder_tasks']] + \
                     [m['id'] for m in cheap_obedient['level_4_complex_tasks']] + \
                     [m['id'] for m in cheap_obedient['level_5_hard_coding_tasks']]

# Create a mapping from model ID to cost
MODEL_COSTS = {}
for model_list in [FREE_TIER_MODELS, PM_MODELS, FREE_WORKER_MODELS, PAID_WORKER_MODELS]:
    for model in model_list:
        # Find the model in the JSON to get its cost
        for section in ['free_tier_models', 'agentic_reasoning_orchestration', 'cheap_obedient_models']:
            if section in MODEL_DATA['open_router_models']:
                section_data = MODEL_DATA['open_router_models'][section]
                if isinstance(section_data, list):
                    for m in section_data:
                        if m['id'] == model:
                            MODEL_COSTS[model] = {'input': m['input_cost'], 'output': m['output_cost']}
                elif isinstance(section_data, dict):
                    for level, models in section_data.items():
                        for m in models:
                            if m['id'] == model:
                                MODEL_COSTS[model] = {'input': m['input_cost'], 'output': m['output_cost']}

# === LOGGING UTIL ===
def log_event(event: str, text: str, agent: str = "system"):
    line = f"[{time.strftime('%H:%M:%S')}] [{agent.upper()}] {event.upper()}: {text.strip()[:5000]}"
    print(line)
    log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manager.log")
    with open(log_file_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# Track costs
TOTAL_COST = 0.0
CURRENT_RUN_COST = 0.0

def get_llm(role: str, worker_type: str = 'free', temperature: float = 0.2) -> ChatOpenAI:
    """
    Factory function to get an LLM instance with fallback logic.
    """
    models_to_try = []
    if role == 'pm':
        models_to_try = PM_MODELS
    elif role == 'worker':
        if worker_type == 'free':
            models_to_try = FREE_TIER_MODELS + FREE_WORKER_MODELS + PAID_WORKER_MODELS
        else:
            models_to_try = FREE_WORKER_MODELS + PAID_WORKER_MODELS
    
    if not API_KEY:
        raise ValueError("OPENAI_API_KEY not set.")

    for model_name in models_to_try:
        try:
            log_event("get_llm", f"Attempting to initialize model '{model_name}' for role '{role}'")
            llm_instance = ChatOpenAI(
                base_url=OPENROUTER_BASE,
                api_key=API_KEY,
                model=model_name,
                temperature=temperature,
                default_headers={"HTTP-Referer": "http://localhost", "X-Title": "LangGraph-Orchestrator"}
            )
            # Test the model with a simple call
            llm_instance.invoke("ping")
            log_event("get_llm_success", f"Successfully initialized model '{model_name}'")
            return llm_instance
        except Exception as e:
            log_event("get_llm_fail", f"Failed to initialize model '{model_name}': {e}")
            continue
    
    raise RuntimeError(f"All LLM models failed for role '{role}'. Please check your API key and model availability.")

def get_model_cost(model_name: str) -> dict:
    """Get the cost per million tokens for a model."""
    return MODEL_COSTS.get(model_name, {'input': 0, 'output': 0})

def update_cost(model_name: str, input_tokens: int, output_tokens: int):
    """Update the total cost based on token usage."""
    global TOTAL_COST, CURRENT_RUN_COST
    cost_info = get_model_cost(model_name)
    cost = (input_tokens / 1_000_000) * cost_info['input'] + (output_tokens / 1_000_000) * cost_info['output']
    TOTAL_COST += cost
    CURRENT_RUN_COST += cost
    log_event("cost_update", f"Model: {model_name}, Input: {input_tokens}, Output: {output_tokens}, Cost: ${cost:.6f}", agent="cost_tracking")

class OrchestratorState(TypedDict):
    goal: str
    target_paths: List[str]
    plan: Optional[str]
    patch: Optional[str]
    test_result: Optional[str]
    test_log: Optional[str]
    iterations: int
    current_task_id: Optional[str]
    task_dependencies: Optional[List[str]]
    sub_tasks: Optional[List[dict]]

