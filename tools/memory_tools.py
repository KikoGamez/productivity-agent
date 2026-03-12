import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from notion_client import Client


def _notion():
    return Client(auth=os.environ.get("NOTION_TOKEN"))


def get_memory() -> str:
    """Read the agent's long-term memory page from Notion."""
    page_id = os.environ.get("NOTION_MEMORY_PAGE_ID", "")
    if not page_id:
        return ""
    try:
        notion = _notion()
        lines = []
        cursor = None
        while True:
            params = {"block_id": page_id, "page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            blocks = notion.blocks.children.list(**params)
            for block in blocks["results"]:
                btype = block["type"]
                if btype in ("paragraph", "heading_1", "heading_2", "heading_3",
                             "bulleted_list_item", "numbered_list_item", "quote"):
                    rich_text = block[btype].get("rich_text", [])
                    text = "".join(rt["plain_text"] for rt in rich_text)
                    if text.strip():
                        lines.append(text)
            if not blocks.get("has_more"):
                break
            cursor = blocks["next_cursor"]
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

        # Collect ALL block IDs (paginated)
        block_ids = []
        cursor = None
        while True:
            params = {"block_id": page_id, "page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            blocks = notion.blocks.children.list(**params)
            block_ids.extend(b["id"] for b in blocks["results"])
            if not blocks.get("has_more"):
                break
            cursor = blocks["next_cursor"]

        # Delete all blocks in parallel (10 concurrent threads)
        if block_ids:
            with ThreadPoolExecutor(max_workers=10) as pool:
                futures = [pool.submit(notion.blocks.delete, block_id=bid) for bid in block_ids]
                for f in as_completed(futures):
                    f.result()  # raise if any failed

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
