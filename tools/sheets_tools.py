import os
from tools.google_auth import get_google_service

SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "")
SHEET_NAME = "Editorial"

# Columns: A=ID, B=Plataforma, C=Título/Temática, D=Artículo, F=Rojo(no), G=Verde(sí)/Lápiz


def get_editorial_articles(only_pending: bool = True) -> list:
    """Read content proposals from the Editorial sheet.
    If only_pending=True, returns only rows without F or G marked.
    """
    service = get_google_service("sheets", "v4")

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEETS_ID, range=f"{SHEET_NAME}!A:G")
        .execute()
    )

    rows = result.get("values", [])
    if not rows:
        return []

    articles = []
    for i, row in enumerate(rows[1:], start=2):  # start=2 because row 1 is header
        if len(row) < 4:
            continue
        content_id = row[0] if len(row) > 0 else ""
        platform  = row[1] if len(row) > 1 else ""
        title     = row[2] if len(row) > 2 else ""
        article   = row[3] if len(row) > 3 else ""
        col_f     = row[5] if len(row) > 5 else ""  # Rojo / No me gusta
        col_g     = row[6] if len(row) > 6 else ""  # Verde / Me gusta / Lápiz

        if not title and not article:
            continue

        status = "pendiente"
        if col_g:
            status = "aprobado con modificaciones" if col_g not in ("TRUE", "VERDADERO", "1") else "aprobado"
        elif col_f:
            status = "rechazado"

        if only_pending and status != "pendiente":
            continue

        articles.append({
            "fila": i,
            "id": content_id,
            "plataforma": platform,
            "titulo": title,
            "articulo": article[:500] + ("..." if len(article) > 500 else ""),
            "estado": status,
        })

    return articles


def mark_article(row: int, action: str) -> str:
    """Mark an article row with the user's decision.
    action: 'aprobar' | 'rechazar' | 'modificar'
    """
    service = get_google_service("sheets", "v4")

    if action == "aprobar":
        # Check G (green), clear F
        requests = [
            {"range": f"{SHEET_NAME}!F{row}", "values": [[""]]},
            {"range": f"{SHEET_NAME}!G{row}", "values": [["TRUE"]]},
        ]
        label = "✅ Aprobado"
    elif action == "rechazar":
        # Check F (red), clear G
        requests = [
            {"range": f"{SHEET_NAME}!F{row}", "values": [["TRUE"]]},
            {"range": f"{SHEET_NAME}!G{row}", "values": [[""]]},
        ]
        label = "❌ Rechazado"
    elif action == "modificar":
        # Mark G with pencil emoji (approved with modifications), clear F
        requests = [
            {"range": f"{SHEET_NAME}!F{row}", "values": [[""]]},
            {"range": f"{SHEET_NAME}!G{row}", "values": [["✏️"]]},
        ]
        label = "✏️ Aprobado con modificaciones"
    else:
        return f"Acción no reconocida: {action}. Usa 'aprobar', 'rechazar' o 'modificar'."

    body = {
        "valueInputOption": "RAW",
        "data": requests,
    }
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEETS_ID, body=body
    ).execute()

    return f"{label} — fila {row} actualizada en Google Sheets."
