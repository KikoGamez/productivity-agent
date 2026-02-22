"""
Script de configuración inicial.
Crea automáticamente las 3 bases de datos de Notion necesarias.

Uso:
  1. Configura NOTION_TOKEN y NOTION_PARENT_PAGE_ID en .env
  2. python setup_notion.py
  3. Copia los IDs generados al .env
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

BRANCH_OPTIONS = [
    {"name": "MIT",                 "color": "blue"},
    {"name": "Intervia.ai",         "color": "green"},
    {"name": "AION Growth Studio",  "color": "purple"},
    {"name": "Marca Personal",      "color": "pink"},
    {"name": "Buscar trabajo",      "color": "orange"},
    {"name": "Networking",          "color": "yellow"},
    {"name": "Personal",            "color": "gray"},
]


def _clean_id(raw_id: str) -> str:
    """Remove dashes from a Notion UUID."""
    return raw_id.replace("-", "")


def create_tasks_db(notion, parent_id: str) -> str:
    db = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_id},
        title=[{"type": "text", "text": {"content": "📋 Tasks"}}],
        properties={
            "Name":            {"title": {}},
            "Branch":          {"select": {"options": BRANCH_OPTIONS}},
            "Status":          {"select": {"options": [
                {"name": "Pending",     "color": "yellow"},
                {"name": "In Progress", "color": "orange"},
                {"name": "Done",        "color": "green"},
            ]}},
            "Priority":        {"select": {"options": [
                {"name": "High",   "color": "red"},
                {"name": "Medium", "color": "yellow"},
                {"name": "Low",    "color": "gray"},
            ]}},
            "Estimated Hours": {"number": {"format": "number"}},
            "Due Date":        {"date": {}},
            "Notes":           {"rich_text": {}},
        },
    )
    return _clean_id(db["id"])


def create_notes_db(notion, parent_id: str) -> str:
    db = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_id},
        title=[{"type": "text", "text": {"content": "📝 Meeting Notes"}}],
        properties={
            "Title":        {"title": {}},
            "Date":         {"date": {}},
            "Attendees":    {"rich_text": {}},
            "Notes":        {"rich_text": {}},
            "Action Items": {"rich_text": {}},
        },
    )
    return _clean_id(db["id"])


def create_time_log_db(notion, parent_id: str) -> str:
    db = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_id},
        title=[{"type": "text", "text": {"content": "⏱️ Time Log"}}],
        properties={
            "Task":   {"title": {}},
            "Branch": {"select": {"options": BRANCH_OPTIONS}},
            "Date":   {"date": {}},
            "Hours":  {"number": {"format": "number"}},
        },
    )
    return _clean_id(db["id"])


def main():
    token = os.environ.get("NOTION_TOKEN")
    parent_id = os.environ.get("NOTION_PARENT_PAGE_ID")

    if not token:
        print("❌ NOTION_TOKEN no está en .env")
        sys.exit(1)

    if not parent_id:
        print("❌ NOTION_PARENT_PAGE_ID no está en .env")
        print("\nCómo obtener el ID de la página padre:")
        print("  1. Abre la página de Notion donde quieres crear las bases de datos")
        print("  2. Copia la URL: notion.so/Mi-Workspace/Mi-Pagina-<ID>")
        print("  3. El ID son los últimos 32 caracteres de la URL (sin guiones)")
        print("  4. También asegúrate de que la integración tiene acceso a esa página")
        sys.exit(1)

    try:
        from notion_client import Client
    except ImportError:
        print("❌ Instala las dependencias primero: pip install -r requirements.txt")
        sys.exit(1)

    notion = Client(auth=token)

    print("🚀 Creando bases de datos en Notion...\n")

    try:
        tasks_id = create_tasks_db(notion, parent_id)
        print(f"✅ Tasks DB         → NOTION_TASKS_DB_ID={tasks_id}")

        notes_id = create_notes_db(notion, parent_id)
        print(f"✅ Meeting Notes DB → NOTION_NOTES_DB_ID={notes_id}")

        time_log_id = create_time_log_db(notion, parent_id)
        print(f"✅ Time Log DB      → NOTION_TIME_LOG_DB_ID={time_log_id}")

        print("\n" + "─" * 55)
        print("Añade esto a tu archivo .env:\n")
        print(f"NOTION_TASKS_DB_ID={tasks_id}")
        print(f"NOTION_NOTES_DB_ID={notes_id}")
        print(f"NOTION_TIME_LOG_DB_ID={time_log_id}")
        print("─" * 55)

    except Exception as exc:
        print(f"\n❌ Error: {exc}")
        print("\nVerifica que:")
        print("  • El NOTION_TOKEN es correcto")
        print("  • La integración tiene acceso a la página padre")
        print("  • El NOTION_PARENT_PAGE_ID es el ID correcto (32 chars, sin guiones)")
        sys.exit(1)


if __name__ == "__main__":
    main()
