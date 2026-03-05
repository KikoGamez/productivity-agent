import base64
from tools.google_auth import get_google_service


def _extract_plain_text(payload: dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    if "parts" in payload:
        # Prefer text/plain part
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="replace"
                    )
        # Fallback: recurse into any part
        for part in payload["parts"]:
            result = _extract_plain_text(part)
            if result:
                return result

    return "[No se pudo extraer el cuerpo del correo]"


def read_emails(max_emails: int = 10, unread_only: bool = True, yesterday_only: bool = False) -> list:
    """Return a list of emails from Gmail primary inbox (no newsletters or commercial emails)."""
    import datetime as _dt
    service = get_google_service("gmail", "v1")

    # category:primary = pestaña Principal de Gmail (excluye promociones, social, actualizaciones)
    query = "in:inbox category:primary"
    if unread_only:
        query += " is:unread"
    if yesterday_only:
        today = _dt.date.today()
        yesterday = today - _dt.timedelta(days=1)
        query += f" after:{yesterday.strftime('%Y/%m/%d')} before:{today.strftime('%Y/%m/%d')}"
    result = (
        service.users()
        .messages()
        .list(userId="me", maxResults=max_emails, q=query)
        .execute()
    )

    messages = result.get("messages", [])
    emails = []
    for msg in messages:
        message = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}
        emails.append(
            {
                "id": msg["id"],
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(Sin asunto)"),
                "date": headers.get("Date", ""),
                "snippet": message.get("snippet", "")[:150],
            }
        )
    return emails


def get_email_body(email_id: str) -> str:
    """Return the full plain-text body of a Gmail message."""
    service = get_google_service("gmail", "v1")
    message = (
        service.users()
        .messages()
        .get(userId="me", id=email_id, format="full")
        .execute()
    )
    return _extract_plain_text(message["payload"])
