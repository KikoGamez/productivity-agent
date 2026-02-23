import os
from notion_client import Client


def _notion():
    return Client(auth=os.environ.get("NOTION_TOKEN"))


def get_memory() -> str:
    """Read the agent's long-term memory page from Notion."""
    page_id = os.environ.get("NOTION_MEMORY_PAGE_ID", "")
    if not page_id:
        return ""
    try:
        blocks = _notion().blocks.children.list(block_id=page_id)
        lines = []
        for block in blocks["results"]:
            btype = block["type"]
            if btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                         "bulleted_list_item", "numbered_list_item", "quote"):
                rich_text = block[btype].get("rich_text", [])
                text = "".join(rt["plain_text"] for rt in rich_text)
                if text.strip():
                    lines.append(text)
        return "\n".join(lines)
    except Exception as e:
        return f"[Error leyendo memoria: {e}]"


def update_memory(new_content: str) -> str:
    """Replace the agent's memory page with updated content."""
    page_id = os.environ.get("NOTION_MEMORY_PAGE_ID", "")
    if not page_id:
        return "Error: NOTION_MEMORY_PAGE_ID no configurado."
    try:
        notion = _notion()
        # Delete all existing blocks
        existing = notion.blocks.children.list(block_id=page_id)
        for block in existing["results"]:
            notion.blocks.delete(block_id=block["id"])

        # Write new content
        children = []
        for line in new_content.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("## "):
                btype, text = "heading_2", stripped[3:]
            elif stripped.startswith("# "):
                btype, text = "heading_1", stripped[2:]
            else:
                btype, text = "paragraph", stripped
            children.append({
                "object": "block",
                "type": btype,
                btype: {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
            })

        for i in range(0, len(children), 100):
            notion.blocks.children.append(block_id=page_id, children=children[i:i+100])

        return "✅ Memoria actualizada."
    except Exception as e:
        return f"Error actualizando memoria: {e}"
