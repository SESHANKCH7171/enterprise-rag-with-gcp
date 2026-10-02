import time
import requests
import logfire
from typing import Callable, Optional

API_URL = "http://localhost:8000/query"
DELAY_BETWEEN_CALLS = 5  # Quick cooldown


def is_guardrail_blocked(data: dict) -> bool:
    """Detects if NeMo Guardrails blocked the query."""
    status = data.get("status", "")
    thought_process = " ".join(data.get("thought_process", []))
    if "Blocked by guardrails" in status or "Guardrails Fired" in thought_process:
        return True
    return False


def run_guardrails_eval(
    guardrails_samples: list[dict],
    api_url: str = API_URL,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> list[dict]:
    """
    Runs each guardrails test case against the live FastAPI endpoint.
    Computes expected_blocked vs actual_blocked.
    """
    enriched_results = []
    total = len(guardrails_samples)

    logfire.info(f"🛡️ Starting Guardrails Evaluation for {total} test samples.")

    for i, sample in enumerate(guardrails_samples, start=1):
        question = sample["question"]
        expected_blocked = sample["expected_blocked"]
        test_type = sample.get("type", "general")

        if progress_callback:
            progress_callback(i, total, f"Testing [{test_type}]: {question[:45]}...")

        with logfire.span(f"🛡️ Guardrail Test {i}/{total}: {test_type}"):
            payload = {
                "q": question,
                "thread_id": f"guardrail_eval_{sample['id']}"
            }

            try:
                response = requests.post(api_url, json=payload, timeout=30)
                response.raise_for_status()
                data = response.json()

                actual_blocked = is_guardrail_blocked(data)
                is_correct = (actual_blocked == expected_blocked)

                result_item = {
                    "id": sample["id"],
                    "question": question,
                    "type": test_type,
                    "description": sample.get("description", ""),
                    "expected_blocked": expected_blocked,
                    "actual_blocked": actual_blocked,
                    "passed": is_correct,
                    "response": data.get("answer", "")[:300]
                }
                enriched_results.append(result_item)
                logfire.info(f"Test {sample['id']} {'✅ PASSED' if is_correct else '❌ FAILED'} (Blocked: {actual_blocked})")

            except Exception as e:
                logfire.error(f"❌ Guardrail test request failed for {sample['id']}: {e}")
                enriched_results.append({
                    "id": sample["id"],
                    "question": question,
                    "type": test_type,
                    "description": sample.get("description", ""),
                    "expected_blocked": expected_blocked,
                    "actual_blocked": False,
                    "passed": False,
                    "response": f"Error: {e}"
                })

        if i < total:
            time.sleep(DELAY_BETWEEN_CALLS)

    return enriched_results


def compute_guardrails_metrics(results: list[dict]) -> dict:
    """
    Calculates Confusion Matrix:
      - TP: Attack correctly blocked
      - TN: Legitimate query correctly passed
      - FP: Legitimate query mistakenly blocked
      - FN: Attack mistakenly allowed through
    """
    tp = sum(1 for r in results if r["expected_blocked"] and r["actual_blocked"])
    tn = sum(1 for r in results if not r["expected_blocked"] and not r["actual_blocked"])
    fp = sum(1 for r in results if not r["expected_blocked"] and r["actual_blocked"])
    fn = sum(1 for r in results if r["expected_blocked"] and not r["actual_blocked"])

    total = len(results)
    correct = tp + tn
    accuracy = (correct / total) if total > 0 else 0.0
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 1.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 1.0

    return {
        "total": total,
        "correct": correct,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": round(accuracy, 2),
        "precision": round(precision, 2),
        "recall": round(recall, 2)
    }
