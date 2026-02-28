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


def get_editorial_style(platform: str = None) -> list:
    """Read editorial style rules from the Estilo sheet.
    Optionally filter by platform (e.g. 'Economía Digital', 'LinkedIn').
    """
    service = get_google_service("sheets", "v4")

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEETS_ID, range="Estilo!A:B")
        .execute()
    )

    rows = result.get("values", [])
    if not rows:
        return []

    rules = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        regla = row[0].strip()
        descripcion = row[1].strip() if len(row) > 1 else ""
        if not descripcion:
            continue
        if platform and regla.lower() not in ("todo", "ambos", "", platform.lower()):
            continue
        rules.append({"regla": regla, "descripcion": descripcion})

    return rules


def get_editorial_references(platform: str = None) -> list:
    """Read reference media from the Referencias sheet.
    Optionally filter by platform (e.g. 'Economía Digital', 'LinkedIn').
    """
    service = get_google_service("sheets", "v4")

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEETS_ID, range="Referencias!A:D")
        .execute()
    )

    rows = result.get("values", [])
    if not rows:
        return []

    references = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        plataforma = row[0].strip()
        nombre = row[1].strip() if len(row) > 1 else ""
        url = row[2].strip() if len(row) > 2 else ""
        notas = row[3].strip() if len(row) > 3 else ""
        if not nombre:
            continue
        if platform and plataforma.lower() not in ("ambos", "todo", "", platform.lower()):
            continue
        references.append({
            "plataforma": plataforma,
            "nombre": nombre,
            "url": url,
            "notas_estilo": notas,
        })

    return references


def _get_sheet_id(service) -> int:
    """Get the numeric sheetId for SHEET_NAME."""
    result = service.spreadsheets().get(spreadsheetId=SHEETS_ID).execute()
    for sheet in result.get("sheets", []):
        if sheet["properties"]["title"] == SHEET_NAME:
            return sheet["properties"]["sheetId"]
    raise ValueError(f"Pestaña '{SHEET_NAME}' no encontrada en el Sheet.")


def mark_article(row: int, action: str) -> str:
    """Mark an article row with the user's decision.
    action: 'aprobar' | 'rechazar' | 'modificar'
    """
    service = get_google_service("sheets", "v4")

    if action == "aprobar":
        label = "✅ Aprobado"
        f_bool, g_bool = False, True
    elif action == "rechazar":
        label = "❌ Rechazado"
        f_bool, g_bool = True, False
    elif action == "modificar":
        label = "✏️ Aprobado con modificaciones"
        # For modify: uncheck F, write pencil emoji in G (not a checkbox value)
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEETS_ID,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": f"{SHEET_NAME}!F{row}", "values": [[""]]},
                    {"range": f"{SHEET_NAME}!G{row}", "values": [["✏️"]]},
                ],
            },
        ).execute()
        return f"{label} — fila {row} actualizada en Google Sheets."
    else:
        return f"Acción no reconocida: {action}. Usa 'aprobar', 'rechazar' o 'modificar'."

    # Use spreadsheets.batchUpdate with repeatCell to set real boolean values
    # (the only API that properly ticks/unticks Google Sheets checkboxes)
    sheet_id = _get_sheet_id(service)
    row_idx = row - 1  # API is 0-indexed

    cell_updates = [
        (5, f_bool),  # column F = index 5
        (6, g_bool),  # column G = index 6
    ]
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": col,
                    "endColumnIndex": col + 1,
                },
                "cell": {
                    "userEnteredValue": {"boolValue": bool_val},
                    "dataValidation": {
                        "condition": {"type": "BOOLEAN"}
                    },
                },
                "fields": "userEnteredValue,dataValidation",
            }
        }
        for col, bool_val in cell_updates
    ]

    service.spreadsheets().batchUpdate(
        spreadsheetId=SHEETS_ID, body={"requests": requests}
    ).execute()

    return f"{label} — fila {row} actualizada en Google Sheets."
