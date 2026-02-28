import os
import httpx

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")


def web_search(query: str) -> str:
    """Search the web using Perplexity Sonar API and return a synthesized answer."""
    if not PERPLEXITY_API_KEY:
        return "Error: PERPLEXITY_API_KEY no configurada en las variables de entorno."

    response = httpx.post(
        "https://api.perplexity.ai/chat/completions",
        headers={
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "sonar",
            "messages": [{"role": "user", "content": query}],
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    content = data["choices"][0]["message"]["content"]

    # Append citations if present
    citations = data.get("citations", [])
    if citations:
        sources = "\n".join(f"[{i+1}] {url}" for i, url in enumerate(citations[:5]))
        content += f"\n\nFuentes:\n{sources}"

    return content
