import time
import copy
import json
import requests
import logfire
from typing import Callable, Optional

API_URL = "http://localhost:8000/query"
MAX_CHARS = 3000
DELAY_BETWEEN_CALLS = 10  # Seconds cooldown between calls to prevent rate-limiting


def detect_tool(thought_process: list[str]) -> str:
    """
    Maps the thought process list from our LangGraph / FastAPI query response
    to a canonical tool name for deterministic Tool Correctness evaluation.
    """
    plan_text = " ".join(thought_process)
    if "Guardrails Fired" in plan_text:
        return "guardrails"
    elif "Intent: Technical" in plan_text or "Search Term:" in plan_text:
        return "retrieve_documents"
    elif "Intent: Conversational" in plan_text or "Retrieval: Skipped" in plan_text:
        return "direct_answer"
    return "unknown"


def run_pipeline(
    dataset: dict, 
    api_url: str = API_URL, 
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> dict:
    """
    Phase 1: Calls the live FastAPI /query endpoint for each sample in the golden dataset.
    Captures:
      - actual_response (cleanly preserved up to 3,000 chars)
      - actual_context (sources from vector DB)
      - actual_tool_calls (mapped from thought_process)
    """
    enriched_dataset = copy.deepcopy(dataset)
    rag_samples = enriched_dataset.get("rag_samples", [])
    total = len(rag_samples)

    logfire.info(f"🚀 Starting Phase 1 Live Execution for {total} Stripe RAG samples.")

    for i, sample in enumerate(rag_samples, start=1):
        question = sample["question"]
        domain = sample.get("domain", "General")
        
        if progress_callback:
            progress_callback(i, total, f"Running [{domain}]: {question[:50]}...")

        with logfire.span(f"🧪 Eval Phase 1 | Sample {i}/{total}: {domain}"):
            payload = {
                "q": question,
                "thread_id": f"eval_session_{sample['id']}"
            }

            try:
                response = requests.post(api_url, json=payload, timeout=60)
                response.raise_for_status()
                data = response.json()

                raw_answer = data.get("answer", "")
                thought_process = data.get("thought_process", [])
                raw_sources = data.get("sources", [])

                # 1. Truncate answer (safe 3000 chars)
                sample["actual_response"] = raw_answer[:MAX_CHARS] if raw_answer else ""

                # 2. Extract actual context texts from sources
                extracted_contexts = []
                for src in raw_sources:
                    if isinstance(src, dict):
                        content = src.get("content", "")
                    else:
                        content = str(src)
                    if content:
                        extracted_contexts.append(content[:MAX_CHARS])
                sample["actual_context"] = extracted_contexts

                # 3. Detect tool called from thought process
                tool_used = detect_tool(thought_process)
                sample["actual_tool_calls"] = [tool_used]

                logfire.info(f"✅ Sample {i}/{total} captured | Tool: {tool_used}")

            except Exception as e:
                logfire.error(f"❌ Failed to query sample {sample['id']}: {e}")
                sample["actual_response"] = f"Error: {e}"
                sample["actual_context"] = []
                sample["actual_tool_calls"] = ["error"]

        # Sleep between calls to respect Groq rate limits
        if i < total:
            time.sleep(DELAY_BETWEEN_CALLS)

    return enriched_dataset


def save_results(dataset: dict, path: str = "evals/goldens.json") -> None:
    """Saves the enriched golden dataset back to disk."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)
    logfire.info(f"💾 Enriched dataset saved to {path}")


def load_golden_dataset(path: str = "evals/goldens.json") -> dict:
    """Loads the golden dataset from disk."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
