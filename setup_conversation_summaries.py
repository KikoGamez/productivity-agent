"""
Crea la base de datos de resúmenes de conversaciones en Notion.
Ejecutar una sola vez: python3 setup_conversation_summaries.py
"""
import os
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")

response = notion.databases.create(
    parent={"type": "page_id", "page_id": parent_page_id},
    title=[{"type": "text", "text": {"content": "🗂️ Resúmenes de Conversaciones"}}],
    properties={
        "Título": {"title": {}},
        "Fecha": {"date": {}},
        "Duración (mensajes)": {"number": {"format": "number"}},
        "Temas": {
            "multi_select": {
                "options": []
            }
        },
        "Personas mencionadas": {
            "multi_select": {
                "options": []
            }
        },
        "Acciones generadas": {"rich_text": {}},
    },
)

db_id = response["id"]
print(f"\n✅ Base de datos 'Resúmenes de Conversaciones' creada correctamente")
print(f"\nAñade esta variable a tu .env y a Railway:")
print(f"\nNOTION_CONV_SUMMARIES_DB_ID={db_id}")
