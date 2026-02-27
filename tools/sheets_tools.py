import os
from tools.google_auth import get_google_service

SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "")


def get_weekly_articles(sheet_name: str = "Sheet1", max_rows: int = 50) -> list:
    """Read weekly recommended articles from Google Sheets."""
    service = get_google_service("sheets", "v4")

    range_notation = f"{sheet_name}!A1:Z{max_rows}"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEETS_ID, range=range_notation)
        .execute()
    )

    rows = result.get("values", [])
    if not rows:
        return []

    # First row as headers
    headers = rows[0]
    articles = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row if cell):
            continue
        article = {}
        for i, header in enumerate(headers):
            article[header] = row[i] if i < len(row) else ""
        articles.append(article)

    return articles
