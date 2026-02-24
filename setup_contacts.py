"""
Crea la base de datos de contactos LinkedIn en Notion.
Ejecutar una sola vez: python3 setup_contacts.py
"""
import os
from notion_client import Client
from dotenv import load_dotenv

load_dotenv()

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")

response = notion.databases.create(
    parent={"type": "page_id", "page_id": parent_page_id},
    title=[{"type": "text", "text": {"content": "🤝 Contactos LinkedIn"}}],
    properties={
        "Persona": {"title": {}},
        "Empresa": {"rich_text": {}},
        "Tipo de contacto": {
            "select": {
                "options": [
                    {"name": "Conexión", "color": "blue"},
                    {"name": "Mensaje", "color": "green"},
                    {"name": "Comentario", "color": "yellow"},
                    {"name": "Reunión", "color": "purple"},
                    {"name": "Café virtual", "color": "orange"},
                    {"name": "Seguimiento", "color": "pink"},
                ]
            }
        },
        "Último contacto": {"date": {}},
        "Próximo contacto": {"rich_text": {}},
        "Fecha próximo contacto": {"date": {}},
        "Estado": {
            "select": {
                "options": [
                    {"name": "Activo", "color": "green"},
                    {"name": "Frío", "color": "gray"},
                    {"name": "Convertido", "color": "blue"},
                ]
            }
        },
    },
)

db_id = response["id"]
print(f"\n✅ Base de datos 'Contactos LinkedIn' creada correctamente")
print(f"\nAñade esta variable a tu .env y a Railway:")
print(f"\nNOTION_CONTACTS_DB_ID={db_id}")
