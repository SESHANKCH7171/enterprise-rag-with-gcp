import os
import sys

# Ensure repository root is at the very front of sys.path before any local imports
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
elif sys.path[0] != REPO_ROOT:
    sys.path.remove(REPO_ROOT)
    sys.path.insert(0, REPO_ROOT)

import asyncio
import pandas as pd
import streamlit as st
import logfire

from evals.pipeline import load_golden_dataset, run_pipeline, save_results
from evals.guardrails_eval import run_guardrails_eval, compute_guardrails_metrics
from evals.metrics import run_all_metrics

# Initialize Logfire
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"))

st.set_page_config(
    page_title="Enterprise Stripe RAG — Evaluation Suite",
    page_icon="🧪",
    layout="wide"
)

# Custom Styling
st.markdown("""
<style>
    .metric-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .score-green { color: #16a34a; font-weight: bold; }
    .score-yellow { color: #ca8a04; font-weight: bold; }
    .score-red { color: #dc2626; font-weight: bold; }
</style>
""", unsafe_allow_html=True)


def color_score(val):
    if isinstance(val, (int, float)):
        if val >= 0.75:
            return 'background-color: #dcfce7; color: #166534;'
        elif val >= 0.50:
            return 'background-color: #fef9c3; color: #854d0e;'
        else:
            return 'background-color: #fee2e2; color: #991b1b;'
    return ''


# Session State Initialization
if "dataset" not in st.session_state:
    try:
        st.session_state.dataset = load_golden_dataset("evals/goldens.json")
    except Exception:
        st.session_state.dataset = {"rag_samples": [], "guardrails_samples": []}

if "pipeline_results" not in st.session_state:
    st.session_state.pipeline_results = None

if "guardrails_results" not in st.session_state:
    st.session_state.guardrails_results = None

if "metrics_df" not in st.session_state:
    st.session_state.metrics_df = None


st.title("🧪 Enterprise Stripe RAG — Evaluation & Security Suite")
st.caption("End-to-End Evaluation: NeMo Guardrails · LangGraph State · Qdrant Vector Search · RAGAS Metrics")

tabs = st.tabs(["📋 Ground Truth Dataset", "⚡ Live Pipeline (Phase 1)", "📊 Evaluation Metrics (Phase 2)"])

# ─── TAB 1: GROUND TRUTH ────────────────────────────────────────────────────────
with tabs[0]:
    st.subheader("Authoritative Stripe Golden Dataset")
    rag_samples = st.session_state.dataset.get("rag_samples", [])
    gr_samples = st.session_state.dataset.get("guardrails_samples", [])

    st.markdown(f"**Total Samples**: `{len(rag_samples)} RAG Samples` · `{len(gr_samples)} Guardrails Security Samples`")

    col1, col2 = st.columns(2)
    with col1:
        st.write("### Stripe RAG Golden Samples")
        if rag_samples:
            df_rag = pd.DataFrame([{
                "ID": s["id"],
                "Domain": s["domain"],
                "Question": s["question"],
                "Reference Ground Truth": s["reference"],
                "Expected Tool": ", ".join(s["expected_tool_calls"])
            } for s in rag_samples])
            st.dataframe(df_rag, use_container_width=True, height=450)

    with col2:
        st.write("### Guardrails Safety & Attack Samples")
        if gr_samples:
            df_gr = pd.DataFrame([{
                "ID": s["id"],
                "Type": s["type"],
                "Question": s["question"],
                "Expected Blocked": "🛡️ BLOCKED" if s["expected_blocked"] else "✅ ALLOWED",
                "Description": s["description"]
            } for s in gr_samples])
            st.dataframe(df_gr, use_container_width=True, height=450)


# ─── TAB 2: LIVE PIPELINE ───────────────────────────────────────────────────────
with tabs[1]:
    st.subheader("Phase 1: Live System Execution & Guardrail Interception")
    st.write("Executes every golden query against your running FastAPI (`http://localhost:8000/query`) backend and captures live responses, sources, and agent state.")

    col_btn1, col_btn2 = st.columns([2, 8])
    with col_btn1:
        run_btn = st.button("🚀 Run Live Pipeline", type="primary", use_container_width=True)
    with col_btn2:
        if st.button("🔄 Reset Live Results", use_container_width=False):
            st.session_state.pipeline_results = None
            st.session_state.guardrails_results = None
            st.rerun()

    if run_btn:
        progress_bar = st.progress(0)
        status_text = st.empty()

        # 1. Execute RAG Pipeline
        def update_rag_progress(curr, total, msg):
            progress_bar.progress(curr / (total * 2))
            status_text.text(f"RAG Evaluation [{curr}/{total}]: {msg}")

        with st.spinner("Executing live RAG queries through FastAPI..."):
            enriched_dataset = run_pipeline(st.session_state.dataset, progress_callback=update_rag_progress)
            st.session_state.dataset = enriched_dataset
            save_results(enriched_dataset, "evals/goldens.json")
            st.session_state.pipeline_results = enriched_dataset.get("rag_samples", [])

        # 2. Execute Guardrails Tests
        def update_gr_progress(curr, total, msg):
            progress_bar.progress(0.5 + (curr / (total * 2)))
            status_text.text(f"Guardrails Testing [{curr}/{total}]: {msg}")

        with st.spinner("Testing safety guardrails & attack interception..."):
            gr_results = run_guardrails_eval(gr_samples, progress_callback=update_gr_progress)
            st.session_state.guardrails_results = gr_results

        progress_bar.progress(1.0)
        status_text.success("✅ Live Phase 1 Pipeline execution completed successfully!")

    # Display Phase 1 Results
    if st.session_state.pipeline_results:
        st.write("### Captured RAG Responses & Tool Calls")
        res_rows = []
        for s in st.session_state.pipeline_results:
            res_rows.append({
                "Domain": s.get("domain"),
                "Question": s.get("question"),
                "Actual Response": s.get("actual_response", "")[:200] + "...",
                "Tool Used": s.get("actual_tool_calls", [""])[0],
                "Sources Retrieved": len(s.get("actual_context", []))
            })
        st.dataframe(pd.DataFrame(res_rows), use_container_width=True)

    if st.session_state.guardrails_results:
        st.write("### Guardrails Confusion Matrix & Safety Metrics")
        cm = compute_guardrails_metrics(st.session_state.guardrails_results)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Safety Accuracy", f"{int(cm['accuracy'] * 100)}%", f"{cm['correct']}/{cm['total']} tests passed")
        m2.metric("Attack Interception Recall", f"{int(cm['recall'] * 100)}%", f"TP: {cm['tp']} | FN: {cm['fn']}")
        m3.metric("Legitimate Precision", f"{int(cm['precision'] * 100)}%", f"FP: {cm['fp']}")
        m4.metric("True Negatives (Allowed)", f"{cm['tn']}")

        st.dataframe(pd.DataFrame(st.session_state.guardrails_results), use_container_width=True)


# ─── TAB 3: EVAL METRICS ────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader("Phase 2: RAGAS & DeepEval Metric Scoring")
    st.write("Runs LLM-as-a-Judge against the captured responses using Groq `llama-3.1-8b-instant` and local sentence transformers.")

    if st.button("📊 Run Evaluation Metrics (Phase 2)", type="primary"):
        if not st.session_state.pipeline_results:
            st.warning("⚠️ Please run Phase 1 first to capture live actual responses!")
        else:
            progress_bar_m = st.progress(0)
            status_text_m = st.empty()

            def update_m_progress(name, step, total):
                progress_bar_m.progress(step / total)
                status_text_m.text(f"Step {step}/{total}: {name}...")

            with st.spinner("Calculating RAGAS metrics & Tool Correctness..."):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                df_scores = loop.run_until_complete(
                    run_all_metrics(st.session_state.dataset, progress_callback=update_m_progress)
                )
                st.session_state.metrics_df = df_scores

            progress_bar_m.progress(1.0)
            status_text_m.success("✅ Metric scoring complete!")

    if st.session_state.metrics_df is not None and not st.session_state.metrics_df.empty:
        df = st.session_state.metrics_df
        numeric_cols = [c for c in df.columns if c not in ["ID", "Domain", "Question"]]
        means = df[numeric_cols].mean()

        st.write("### Benchmark KPI Summary")
        kpis = st.columns(len(numeric_cols))
        for col, kpi in zip(numeric_cols, kpis):
            score = means[col]
            color = "🟢" if score >= 0.75 else "🟡" if score >= 0.50 else "🔴"
            kpi.metric(f"{color} {col}", f"{score:.2f}")

        st.write("### Detailed Per-Sample Scores")
        style_mapper = getattr(df.style, "map", getattr(df.style, "applymap", None))
        styled_df = style_mapper(color_score, subset=numeric_cols) if style_mapper else df
        st.dataframe(styled_df, use_container_width=True)
