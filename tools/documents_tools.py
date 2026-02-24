import os
from datetime import datetime
from notion_client import Client

notion = Client(auth=os.environ.get("NOTION_TOKEN", ""))
DOCS_DB_ID = os.environ.get("NOTION_DOCS_DB_ID", "")


def save_document(title: str, content: str, tags: list = None, source: str = "Manual") -> str:
    """Save a document to the Notion documents database."""
    properties = {
        "Título": {"title": [{"text": {"content": title}}]},
        "Fuente": {"select": {"name": source}},
        "Fecha": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
    }
    if tags:
        properties["Etiquetas"] = {"multi_select": [{"name": t} for t in tags]}

    # Split content into 2000-char blocks (Notion limit per rich_text block)
    chunks = [content[i:i+2000] for i in range(0, len(content), 2000)]
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
        }
        for chunk in chunks
    ]

    notion.pages.create(
        parent={"database_id": DOCS_DB_ID},
        properties=properties,
        children=children[:100],  # Notion limit: 100 blocks per request
    )
    return f"✅ Documento '{title}' guardado en Notion."


def search_documents(query: str = "", tags: list = None) -> list:
    """Search documents by title or tags."""
    filters = []
    if query:
        filters.append({"property": "Título", "title": {"contains": query}})
    if tags:
        for tag in tags:
            filters.append({"property": "Etiquetas", "multi_select": {"contains": tag}})

    query_params = {
        "database_id": DOCS_DB_ID,
        "page_size": 20,
        "sorts": [{"property": "Fecha", "direction": "descending"}],
    }
    if filters:
        query_params["filter"] = {"and": filters} if len(filters) > 1 else filters[0]

    results = notion.databases.query(**query_params)
    docs = []
    for page in results.get("results", []):
        props = page["properties"]
        title_items = (props.get("Título") or {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_items)
        tags_items = (props.get("Etiquetas") or {}).get("multi_select", [])
        doc_tags = [t["name"] for t in tags_items]
        date = ((props.get("Fecha") or {}).get("date") or {}).get("start", "")
        source = ((props.get("Fuente") or {}).get("select") or {}).get("name", "")
        docs.append({"id": page["id"], "title": title, "date": date, "source": source, "tags": doc_tags})
    return docs


def get_document_content(doc_id: str) -> str:
    """Get the full content of a document by its page ID."""
    blocks = notion.blocks.children.list(block_id=doc_id)
    lines = []
    for block in blocks.get("results", []):
        btype = block["type"]
        if btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                     "bulleted_list_item", "numbered_list_item"):
            rich_text = block[btype].get("rich_text", [])
            text = "".join(rt["plain_text"] for rt in rich_text)
            if text.strip():
                lines.append(text)
    return "\n".join(lines) or "[Documento vacío]"
