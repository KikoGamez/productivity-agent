"""
Crea la página de memoria del agente en Notion.
Ejecutar una sola vez: python3 setup_memory.py
"""
import os
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")

response = notion.pages.create(
    parent={"type": "page_id", "page_id": parent_page_id},
    properties={
        "title": [{"type": "text", "text": {"content": "🧠 Memoria del Agente"}}]
    },
    children=[
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Contexto Personal"}}]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "El agente irá actualizando esta página automáticamente con información relevante sobre proyectos, contactos y compromisos."}}]},
        },
    ],
)

page_id = response["id"]
print(f"\n✅ Página de memoria creada correctamente")
print(f"\nAñade esta variable a tu .env y a Railway:")
print(f"\nNOTION_MEMORY_PAGE_ID={page_id}")
