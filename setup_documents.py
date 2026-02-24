"""
Crea la base de datos de documentos en Notion.
Ejecutar una sola vez: python3 setup_documents.py
"""
import os
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")

response = notion.databases.create(
    parent={"type": "page_id", "page_id": parent_page_id},
    title=[{"type": "text", "text": {"content": "📚 Documentos"}}],
    properties={
        "Título": {"title": {}},
        "Fuente": {
            "select": {
                "options": [
                    {"name": "Manual", "color": "blue"},
                    {"name": "Email", "color": "green"},
                    {"name": "Reunión", "color": "purple"},
                    {"name": "Audio", "color": "orange"},
                    {"name": "Investigación", "color": "yellow"},
                ]
            }
        },
        "Etiquetas": {"multi_select": {"options": []}},
        "Fecha": {"date": {}},
    },
)

db_id = response["id"]
print(f"\n✅ Base de datos 'Documentos' creada correctamente")
print(f"\nAñade esta variable a tu .env y a Railway:")
print(f"\nNOTION_DOCS_DB_ID={db_id}")
