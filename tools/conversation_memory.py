import os
from datetime import datetime, timedelta
from notion_client import Client


def _notion():
    return Client(auth=os.environ.get("NOTION_TOKEN", ""))


CONV_SUMMARIES_DB_ID = os.environ.get("NOTION_CONV_SUMMARIES_DB_ID", "")


def save_conversation_summary(
    title: str,
    summary_text: str,
    num_messages: int,
    temas: list[str],
    personas: list[str],
    acciones: str,
) -> str:
    """Save a conversation session summary to the Notion database."""
    if not CONV_SUMMARIES_DB_ID:
        return "NOTION_CONV_SUMMARIES_DB_ID no configurado, resumen no guardado."

    properties = {
        "Título": {"title": [{"text": {"content": title[:100]}}]},
        "Fecha": {"date": {"start": datetime.now().isoformat()}},
        "Duración (mensajes)": {"number": num_messages},
        "Temas": {"multi_select": [{"name": t[:100]} for t in temas[:10]]},
        "Personas mencionadas": {"multi_select": [{"name": p[:100]} for p in personas[:10]]},
        "Acciones generadas": {
            "rich_text": [{"type": "text", "text": {"content": acciones[:2000]}}]
        },
    }

    # Split summary into 2000-char blocks (Notion limit per rich_text block)
    chunks = [summary_text[i : i + 2000] for i in range(0, len(summary_text), 2000)]
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
        }
        for chunk in chunks
    ]

    _notion().pages.create(
        parent={"database_id": CONV_SUMMARIES_DB_ID},
        properties=properties,
        children=children[:100],
    )
    return f"✅ Resumen '{title}' guardado."


def get_recent_summaries(limit: int = 5) -> list[dict]:
    """Get the N most recent conversation summaries with full body text."""
    if not CONV_SUMMARIES_DB_ID:
        return []

    notion = _notion()
    results = notion.databases.query(
        database_id=CONV_SUMMARIES_DB_ID,
        page_size=limit,
        sorts=[{"property": "Fecha", "direction": "descending"}],
    )

    summaries = []
    for page in results.get("results", []):
        props = page["properties"]
        title_items = (props.get("Título") or {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_items)
        date = ((props.get("Fecha") or {}).get("date") or {}).get("start", "")
        temas = [t["name"] for t in (props.get("Temas") or {}).get("multi_select", [])]

        # Read page body for the full narrative
        body = _read_page_body(notion, page["id"])

        summaries.append({
            "id": page["id"],
            "title": title,
            "date": date,
            "temas": temas,
            "summary_text": body,
        })
    return summaries


def search_summaries(
    tema: str = None, persona: str = None, dias: int = 30
) -> list[dict]:
    """Search conversation summaries by topic, person, or date range."""
    if not CONV_SUMMARIES_DB_ID:
        return []

    filters = []
    if tema:
        filters.append({"property": "Temas", "multi_select": {"contains": tema}})
    if persona:
        filters.append(
            {"property": "Personas mencionadas", "multi_select": {"contains": persona}}
        )
    if dias:
        since = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
        filters.append({"property": "Fecha", "date": {"on_or_after": since}})

    query_params = {
        "database_id": CONV_SUMMARIES_DB_ID,
        "page_size": 20,
        "sorts": [{"property": "Fecha", "direction": "descending"}],
    }
    if filters:
        query_params["filter"] = {"and": filters} if len(filters) > 1 else filters[0]

    results = _notion().databases.query(**query_params)
    summaries = []
    for page in results.get("results", []):
        props = page["properties"]
        title_items = (props.get("Título") or {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_items)
        date = ((props.get("Fecha") or {}).get("date") or {}).get("start", "")
        temas = [t["name"] for t in (props.get("Temas") or {}).get("multi_select", [])]
        personas = [
            p["name"]
            for p in (props.get("Personas mencionadas") or {}).get("multi_select", [])
        ]
        acciones_rt = (props.get("Acciones generadas") or {}).get("rich_text", [])
        acciones = "".join(a.get("plain_text", "") for a in acciones_rt)
        summaries.append({
            "id": page["id"],
            "title": title,
            "date": date,
            "temas": temas,
            "personas": personas,
            "acciones": acciones,
        })
    return summaries


def get_summary_content(summary_id: str) -> str:
    """Get the full narrative body of a conversation summary."""
    if not CONV_SUMMARIES_DB_ID:
        return "[NOTION_CONV_SUMMARIES_DB_ID no configurado]"
    return _read_page_body(_notion(), summary_id)


def _read_page_body(notion: Client, page_id: str) -> str:
    """Read all text blocks from a Notion page body."""
    blocks = notion.blocks.children.list(block_id=page_id)
    lines = []
    for block in blocks.get("results", []):
        btype = block["type"]
        if btype in (
            "paragraph", "heading_1", "heading_2", "heading_3",
            "bulleted_list_item", "numbered_list_item",
        ):
            rich_text = block[btype].get("rich_text", [])
            text = "".join(rt["plain_text"] for rt in rich_text)
            if text.strip():
                lines.append(text)
    return "\n".join(lines) or "[Resumen vacío]"
