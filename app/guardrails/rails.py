import logfire
from langchain_groq import ChatGroq
from nemoguardrails import RailsConfig, LLMRails

from app.config import settings
from app.guardrails.colang_rules import COLANG_CONTENT, YAML_CONTENT, RAIL_INDICATORS


_rails: LLMRails | None = None


def initialize_rails() -> None:
    """
    Build the NeMo LLMRails singleton.
    Uses settings.GROQ_FAST_MODEL for fast, accurate intent classification at the gate.
    """
    global _rails

    guard_llm = ChatGroq(
        api_key=settings.GROQ_API_KEY,
        model=settings.GROQ_FAST_MODEL,
        temperature=0
    )

    config = RailsConfig.from_content(
        colang_content=COLANG_CONTENT,
        yaml_content=YAML_CONTENT
    )

    _rails = LLMRails(config, llm=guard_llm)
    logfire.info(f"🛡️ NeMo Guardrails initialised ({settings.GROQ_FAST_MODEL}).")


def is_rail_triggered(content: str) -> bool:
    """Detects whether NeMo Rails or the underlying safety policy intercepted the request."""
    # Normalize curly apostrophes / quotes
    normalized = content.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').lower()

    # 1. Match configured Colang bot indicators
    for ind in RAIL_INDICATORS:
        norm_ind = ind.replace("’", "'").replace("‘", "'").lower()
        if norm_ind in normalized:
            return True

    # 2. Check the response text (after any chain-of-thought </think> tag) for safety refusals
    clean_text = normalized.split("</think>")[-1].strip() if "</think>" in normalized else normalized
    refusal_patterns = [
        "can't help with that",
        "cannot help with that",
        "cannot assist",
        "i'm sorry, but",
        "i am sorry, but",
        "cannot disclose",
        "cannot fulfill",
        "not permitted",
        "against policy",
        "cannot provide",
        "i cannot help",
        "unable to assist",
        "dedicated solely to stripe",
    ]
    if any(pat in clean_text for pat in refusal_patterns):
        return True

    return False


def guard(message: str) -> tuple[bool, str | None]:
    """
    Run a user message through the NeMo rails gate.

    Returns:
        (True,  rail_response) — a rail fired; return this response immediately,
                                skip the RAG pipeline entirely.
        (False, None)          — message is clean; proceed to LangGraph.
    """
    global _rails
    if _rails is None:
        initialize_rails()

    if _rails is None:
        logfire.warning("⚠️ Guardrails not initialised — skipping gate.")
        return False, None

    with logfire.span("🛡️ Guardrails Check"):
        result = _rails.generate(messages=[{"role": "user", "content": message}])

        # NeMo returns {'role': 'assistant', 'content': '...'} — extract text
        content = result.get("content", "") if isinstance(result, dict) else str(result)

        if is_rail_triggered(content):
            logfire.info(f"🛡️ Guardrails fired | query='{message[:80]}'")
            # If the model produced a <think> tag, strip it for clean user presentation
            clean_content = content
            if "</think>" in clean_content:
                clean_content = clean_content.split("</think>")[-1].strip()
            return True, clean_content

        logfire.info("✅ Guardrails passed.")
        return False, None
