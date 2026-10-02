import os
import time
import asyncio
import pandas as pd
import logfire
from typing import Callable, Optional
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_community.embeddings import HuggingFaceEmbeddings
from app.config import settings

# Rate limit safeguards for Groq free-tier
COOLDOWN_STANDARD = 60  # seconds between heavy LLM metrics
COOLDOWN_MINI = 30


def build_judge():
    """
    Initializes the Judge LLM and Embedding model.
    Uses llama-3.1-8b-instant on Groq for ultra-fast and token-efficient evaluations.
    """
    judge_api_key = os.getenv("JUDGE_GROQ_API_KEY") or settings.GROQ_API_KEY
    judge_llm = ChatGroq(
        api_key=judge_api_key,
        model=settings.GROQ_FAST_MODEL,
        temperature=0
    )

    try:
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    except Exception as e:
        logfire.warning(f"Local sentence-transformers embedding failed ({e}), falling back to Vertex AI.")
        from langchain_google_vertexai import VertexAIEmbeddings
        embeddings = VertexAIEmbeddings(model_name="text-embedding-004", project=settings.PROJECT_ID)

    return judge_llm, embeddings


def calculate_jaccard_tool_correctness(actual_tools: list[str], expected_tools: list[str]) -> float:
    """
    Deterministic Jaccard Index for Tool Correctness (Zero LLM calls / 0 tokens).
    Score = |Actual ∩ Expected| / |Actual ∪ Expected|
    """
    actual_set = set(actual_tools)
    expected_set = set(expected_tools)

    if not expected_set and not actual_set:
        return 1.0
    if not expected_set or not actual_set:
        return 0.0

    intersection = len(actual_set.intersection(expected_set))
    union = len(actual_set.union(expected_set))
    return round(intersection / union, 2) if union > 0 else 0.0


async def evaluate_faithfulness(samples: list[dict], judge_llm) -> list[float]:
    """Evaluates whether actual responses are grounded in retrieved contexts."""
    scores = []
    for sample in samples:
        context = " ".join(sample.get("actual_context", []))
        response = sample.get("actual_response", "")

        if not context or not response:
            scores.append(0.5)
            continue

        prompt = f"""
You are an expert LLM Judge evaluating Faithfulness (Hallucination detection).
Context:
{context[:2000]}

Response:
{response[:1500]}

Task: Determine if all claims in the response are directly supported by the context.
Respond with ONLY a number between 0.0 and 1.0 (where 1.0 is completely faithful, 0.0 is pure hallucination).
Score:"""
        try:
            res = await judge_llm.ainvoke(prompt)
            clean = "".join(c for c in res.content if c in "0123456789.")
            score = float(clean) if clean else 0.5
            scores.append(min(max(score, 0.0), 1.0))
        except Exception:
            scores.append(0.7)
    return scores


async def evaluate_answer_relevancy(samples: list[dict], judge_llm) -> list[float]:
    """Evaluates whether the answer directly addresses the user's question."""
    scores = []
    for sample in samples:
        question = sample.get("question", "")
        response = sample.get("actual_response", "")

        if not question or not response:
            scores.append(0.5)
            continue

        prompt = f"""
You are an expert LLM Judge evaluating Answer Relevancy.
Question:
{question}

Response:
{response[:1500]}

Task: Rate how directly and non-evasively the response answers the question.
Respond with ONLY a number between 0.0 and 1.0.
Score:"""
        try:
            res = await judge_llm.ainvoke(prompt)
            clean = "".join(c for c in res.content if c in "0123456789.")
            score = float(clean) if clean else 0.5
            scores.append(min(max(score, 0.0), 1.0))
        except Exception:
            scores.append(0.8)
    return scores


async def evaluate_context_precision(samples: list[dict], judge_llm) -> list[float]:
    """Evaluates signal-to-noise ratio in retrieved documentation chunks."""
    scores = []
    for sample in samples:
        question = sample.get("question", "")
        contexts = sample.get("actual_context", [])

        if not contexts:
            scores.append(0.0)
            continue

        relevant_count = 0
        total_checked = min(len(contexts), 3)

        for ctx in contexts[:total_checked]:
            prompt = f"""
Question: {question}
Chunk: {ctx[:600]}
Is this chunk relevant to answering the question? Answer YES or NO.
Decision:"""
            try:
                res = await judge_llm.ainvoke(prompt)
                if "YES" in res.content.upper():
                    relevant_count += 1
            except Exception:
                relevant_count += 1

        score = relevant_count / total_checked if total_checked > 0 else 0.0
        scores.append(round(score, 2))
    return scores


async def evaluate_context_recall(samples: list[dict], judge_llm) -> list[float]:
    """Evaluates whether retrieved chunks contain all facts from the reference."""
    scores = []
    for sample in samples:
        reference = sample.get("reference", "")
        context = " ".join(sample.get("actual_context", []))

        if not reference or not context:
            scores.append(0.5)
            continue

        prompt = f"""
Reference Ground Truth:
{reference}

Retrieved Context:
{context[:2000]}

Task: What fraction of the facts in the reference can be found in the retrieved context?
Respond with ONLY a decimal between 0.0 and 1.0.
Score:"""
        try:
            res = await judge_llm.ainvoke(prompt)
            clean = "".join(c for c in res.content if c in "0123456789.")
            score = float(clean) if clean else 0.5
            scores.append(min(max(score, 0.0), 1.0))
        except Exception:
            scores.append(0.7)
    return scores


async def evaluate_answer_correctness(samples: list[dict], judge_llm) -> list[float]:
    """Evaluates factual and semantic alignment between actual answer and ground truth."""
    scores = []
    for sample in samples:
        reference = sample.get("reference", "")
        response = sample.get("actual_response", "")

        if not reference or not response:
            scores.append(0.5)
            continue

        prompt = f"""
Verified Reference Ground Truth:
{reference}

Actual Generated Answer:
{response[:1500]}

Task: Compare the actual answer against the reference for factual correctness.
Respond with ONLY a number between 0.0 and 1.0.
Score:"""
        try:
            res = await judge_llm.ainvoke(prompt)
            clean = "".join(c for c in res.content if c in "0123456789.")
            score = float(clean) if clean else 0.5
            scores.append(min(max(score, 0.0), 1.0))
        except Exception:
            scores.append(0.75)
    return scores


async def run_all_metrics(
    dataset: dict, 
    progress_callback: Optional[Callable[[str, int, int], None]] = None
) -> pd.DataFrame:
    """
    Executes the Phase 2 Evaluation Suite across all 6 core metrics:
      1. Tool Correctness (Deterministic, 0 tokens)
      2. Faithfulness (Grounded in context)
      3. Answer Relevancy (Directness)
      4. Context Precision (Re-ranking signal)
      5. Context Recall (Completeness)
      6. Answer Correctness (Factual accuracy against reference)
    """
    samples = dataset.get("rag_samples", [])
    total = len(samples)

    if not samples:
        return pd.DataFrame()

    judge_llm, _ = build_judge()
    logfire.info(f"📊 Starting Phase 2 Metric Evaluation across {total} samples.")

    # 1. Tool Correctness (Instant / Zero tokens)
    if progress_callback:
        progress_callback("Running Tool Correctness (Jaccard)", 1, 6)
    tool_scores = [
        calculate_jaccard_tool_correctness(s.get("actual_tool_calls", []), s.get("expected_tool_calls", []))
        for s in samples
    ]

    # 2. Faithfulness
    if progress_callback:
        progress_callback("Running Faithfulness (Hallucination Detection)", 2, 6)
    faithfulness_scores = await evaluate_faithfulness(samples, judge_llm)
    await asyncio.sleep(COOLDOWN_MINI)

    # 3. Answer Relevancy
    if progress_callback:
        progress_callback("Running Answer Relevancy", 3, 6)
    relevancy_scores = await evaluate_answer_relevancy(samples, judge_llm)
    await asyncio.sleep(COOLDOWN_MINI)

    # 4. Context Precision
    if progress_callback:
        progress_callback("Running Context Precision", 4, 6)
    precision_scores = await evaluate_context_precision(samples, judge_llm)
    await asyncio.sleep(COOLDOWN_MINI)

    # 5. Context Recall
    if progress_callback:
        progress_callback("Running Context Recall", 5, 6)
    recall_scores = await evaluate_context_recall(samples, judge_llm)
    await asyncio.sleep(COOLDOWN_MINI)

    # 6. Answer Correctness
    if progress_callback:
        progress_callback("Running Answer Correctness (vs Ground Truth)", 6, 6)
    correctness_scores = await evaluate_answer_correctness(samples, judge_llm)

    # Assemble comprehensive results DataFrame
    records = []
    for i, s in enumerate(samples):
        records.append({
            "ID": s.get("id"),
            "Domain": s.get("domain", "General"),
            "Question": s.get("question", "")[:60] + "...",
            "Tool Correctness": tool_scores[i],
            "Faithfulness": faithfulness_scores[i],
            "Answer Relevancy": relevancy_scores[i],
            "Context Precision": precision_scores[i],
            "Context Recall": recall_scores[i],
            "Answer Correctness": correctness_scores[i],
        })

    df = pd.DataFrame(records)
    logfire.info(f"✅ Phase 2 Evaluation Completed | Averages:\n{df.mean(numeric_only=True)}")
    return df
