"""
Lightweight RAG: automatically retrieve relevant documents from Notion
based on keywords in the user's message, without embeddings or external services.
"""
import re
from tools.documents_tools import search_documents, get_document_content

# Spanish stop words to ignore when extracting keywords
STOP_WORDS = {
    "a", "al", "algo", "ante", "antes", "como", "con", "cual", "cuando", "de",
    "del", "desde", "donde", "durante", "el", "ella", "ellas", "ellos", "en",
    "entre", "era", "es", "esta", "este", "esto", "estos", "estas", "fue",
    "han", "has", "hay", "he", "hola", "la", "las", "le", "les", "lo", "los",
    "me", "mi", "mis", "muy", "no", "nos", "o", "para", "pero", "por", "que",
    "se", "si", "sin", "sobre", "su", "sus", "también", "te", "tengo", "ti",
    "toda", "todo", "todos", "tu", "tus", "un", "una", "uno", "unos", "unas",
    "y", "ya", "yo", "qué", "cómo", "cuál", "quién", "cuándo", "dónde",
    "puedo", "puede", "quiero", "quiere", "necesito", "hacer", "haz", "dame",
    "dime", "diles", "tengo", "tiene", "hay", "ver", "mira", "míralo",
}


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from a message."""
    words = re.findall(r'\b[a-záéíóúüñA-ZÁÉÍÓÚÜÑ]{4,}\b', text)
    return [w.lower() for w in words if w.lower() not in STOP_WORDS]


def get_relevant_context(user_message: str, max_docs: int = 2, max_chars_per_doc: int = 3000) -> str:
    """
    Search for documents relevant to the user's message and return
    a formatted context string to inject into the system prompt.
    Returns empty string if nothing relevant is found.
    """
    keywords = _extract_keywords(user_message)
    if not keywords:
        return ""

    seen_ids = set()
    relevant_docs = []

    # Search by each keyword, deduplicate by doc ID
    for keyword in keywords[:5]:  # Limit to 5 keywords max
        try:
            results = search_documents(query=keyword)
            for doc in results:
                if doc["id"] not in seen_ids:
                    seen_ids.add(doc["id"])
                    relevant_docs.append(doc)
        except Exception:
            continue

    if not relevant_docs:
        return ""

    # Fetch content of the top matches
    context_parts = []
    for doc in relevant_docs[:max_docs]:
        try:
            content = get_document_content(doc["id"])
            snippet = content[:max_chars_per_doc]
            if len(content) > max_chars_per_doc:
                snippet += "..."
            tags_str = f" [{', '.join(doc['tags'])}]" if doc.get("tags") else ""
            context_parts.append(f"📄 {doc['title']}{tags_str} ({doc.get('date', '')}):\n{snippet}")
        except Exception:
            continue

    if not context_parts:
        return ""

    return "DOCUMENTOS RELEVANTES RECUPERADOS AUTOMÁTICAMENTE:\n" + "\n\n".join(context_parts)
