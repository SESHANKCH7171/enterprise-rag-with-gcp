import logfire
from langchain_groq import ChatGroq
from groq import Groq
from app.config import settings

# Detect if a genuine Portkey API key is provided
_has_portkey = bool(
    settings.PORTKEY_API_KEY
    and not settings.PORTKEY_API_KEY.startswith("placeholder")
    and len(settings.PORTKEY_API_KEY) > 10
)

if _has_portkey:
    from portkey_ai import Portkey, createHeaders, PORTKEY_GATEWAY_URL
    from langchain_openai import ChatOpenAI

    GATEWAY_CONFIG = {
        "strategy": {"mode": "fallback"},
        "cache": {"mode": "simple"},
        "retry": {
            "attempts": 2,
            "on_status_codes": [429, 503]
        },
        "targets": [
            {"override_params": {"model": f"@{settings.GROQ_SLUG}/{settings.GROQ_MODEL}"}},
            {"override_params": {"model": f"@{settings.GROQ_SLUG_2}/{settings.GROQ_FAST_MODEL}"}},
        ]
    }

    portkey_client = Portkey(
        api_key=settings.PORTKEY_API_KEY,
        config=GATEWAY_CONFIG
    )

    def get_langchain_llm(feature: str = "rag") -> ChatOpenAI:
        return ChatOpenAI(
            api_key=settings.PORTKEY_API_KEY,
            base_url=PORTKEY_GATEWAY_URL,
            model=f"@{settings.GROQ_SLUG}/{settings.GROQ_MODEL}",
            temperature=0,
            default_headers=createHeaders(
                api_key=settings.PORTKEY_API_KEY,
                config=GATEWAY_CONFIG,
                metadata={
                    "feature": feature,
                    "_user": "rag-system",
                    "environment": "production"
                }
            )
        )
else:
    # Direct Groq Gateway Fallback (Zero Portkey key dependency)
    class _GroqCompletionsWrapper:
        def __init__(self, client: Groq):
            self._client = client

        def create(self, **kwargs):
            if "model" not in kwargs or kwargs.get("model", "").startswith("@"):
                kwargs["model"] = settings.GROQ_MODEL
            return self._client.chat.completions.create(**kwargs)

    class _GroqChatWrapper:
        def __init__(self, client: Groq):
            self.completions = _GroqCompletionsWrapper(client)

    class DirectGroqClient:
        def __init__(self):
            self._client = Groq(api_key=settings.GROQ_API_KEY)
            self.chat = _GroqChatWrapper(self._client)

    portkey_client = DirectGroqClient()

    def get_langchain_llm(feature: str = "rag"):
        """Returns direct ChatGroq configured with current production Groq model."""
        return ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
            temperature=0
        )


def extract_cache_status(response) -> str:
    """
    Pull x-portkey-cache-status from the Portkey response headers.
    Returns 'MISS' when direct Groq or cache misses.
    """
    for attr in ("_raw_response", "_response", "_http_response"):
        raw = getattr(response, attr, None)
        if raw is not None:
            status = getattr(raw, "headers", {}).get("x-portkey-cache-status", "")
            if status:
                return status.upper()
    return "MISS"