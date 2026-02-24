import os
from datetime import datetime
from notion_client import Client

notion = Client(auth=os.environ.get("NOTION_TOKEN", ""))
CONTACTS_DB_ID = os.environ.get("NOTION_CONTACTS_DB_ID", "")


def _get_text(prop) -> str:
    items = prop.get("rich_text") or prop.get("title") or []
    return "".join(item.get("plain_text", "") for item in items)


def add_contact(
    persona: str,
    empresa: str = "",
    tipo_contacto: str = "Conexión",
    ultimo_contacto: str = None,
    proximo_contacto: str = "",
    fecha_proximo_contacto: str = None,
) -> str:
    """Add a LinkedIn contact to the Notion database."""
    today = datetime.now().strftime("%Y-%m-%d")
    properties = {
        "Persona": {"title": [{"text": {"content": persona}}]},
        "Empresa": {"rich_text": [{"text": {"content": empresa}}]},
        "Tipo de contacto": {"select": {"name": tipo_contacto}},
        "Último contacto": {"date": {"start": ultimo_contacto or today}},
        "Estado": {"select": {"name": "Activo"}},
    }
    if proximo_contacto:
        properties["Próximo contacto"] = {"rich_text": [{"text": {"content": proximo_contacto}}]}
    if fecha_proximo_contacto:
        properties["Fecha próximo contacto"] = {"date": {"start": fecha_proximo_contacto}}

    notion.pages.create(parent={"database_id": CONTACTS_DB_ID}, properties=properties)
    return f"✅ Contacto '{persona}' ({empresa}) añadido."


def get_contacts(estado: str = None, dias_sin_contacto: int = None) -> list:
    """Get LinkedIn contacts, optionally filtered by status or days since last contact."""
    filters = []
    if estado:
        filters.append({"property": "Estado", "select": {"equals": estado}})
    if dias_sin_contacto:
        from datetime import timedelta
        fecha_limite = (datetime.now() - timedelta(days=dias_sin_contacto)).strftime("%Y-%m-%d")
        filters.append({"property": "Último contacto", "date": {"before": fecha_limite}})

    query_params = {
        "database_id": CONTACTS_DB_ID,
        "page_size": 100,
        "sorts": [{"property": "Fecha próximo contacto", "direction": "ascending"}],
    }
    if filters:
        query_params["filter"] = {"and": filters} if len(filters) > 1 else filters[0]

    results = notion.databases.query(**query_params)
    contacts = []
    for page in results.get("results", []):
        props = page["properties"]
        contact = {
            "id": page["id"],
            "persona": _get_text(props.get("Persona", {})),
            "empresa": _get_text(props.get("Empresa", {})),
            "tipo_contacto": (props.get("Tipo de contacto") or {}).get("select", {}).get("name", ""),
            "estado": (props.get("Estado") or {}).get("select", {}).get("name", ""),
            "proximo_contacto": _get_text(props.get("Próximo contacto", {})),
        }
        ultimo = (props.get("Último contacto") or {}).get("date")
        if ultimo:
            contact["ultimo_contacto"] = ultimo.get("start", "")
        fecha_prox = (props.get("Fecha próximo contacto") or {}).get("date")
        if fecha_prox:
            contact["fecha_proximo_contacto"] = fecha_prox.get("start", "")
        contacts.append(contact)
    return contacts


def update_contact(
    contact_id: str,
    tipo_contacto: str = None,
    ultimo_contacto: str = None,
    proximo_contacto: str = None,
    fecha_proximo_contacto: str = None,
    estado: str = None,
) -> str:
    """Update a LinkedIn contact's fields by page ID."""
    today = datetime.now().strftime("%Y-%m-%d")
    properties = {}

    if tipo_contacto:
        properties["Tipo de contacto"] = {"select": {"name": tipo_contacto}}
    if ultimo_contacto or tipo_contacto:
        properties["Último contacto"] = {"date": {"start": ultimo_contacto or today}}
    if proximo_contacto is not None:
        properties["Próximo contacto"] = {"rich_text": [{"text": {"content": proximo_contacto}}]}
    if fecha_proximo_contacto:
        properties["Fecha próximo contacto"] = {"date": {"start": fecha_proximo_contacto}}
    if estado:
        properties["Estado"] = {"select": {"name": estado}}

    notion.pages.update(page_id=contact_id, properties=properties)
    return f"✅ Contacto actualizado correctamente."
