import os
from datetime import datetime
from tools.google_auth import get_google_service

TIMEZONE = os.environ.get("TIMEZONE", "Europe/Madrid")

BRANCH_COLORS = {
    "MIT":                 "1",   # Azul
    "Intervia.ai":         "2",   # Verde
    "AION Growth Studio":  "3",   # Morado
    "Marca Personal":      "4",   # Rosa/rojo
    "Buscar trabajo":      "5",   # Amarillo
    "Networking":          "6",   # Naranja
    "Personal":            "8",   # Grafito
}


def get_calendar_events(date: str = None) -> list:
    """Return Google Calendar events for the given date (default: today)."""
    service = get_google_service("calendar", "v3")

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    time_min = f"{date}T00:00:00Z"
    time_max = f"{date}T23:59:59Z"

    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = []
    for event in result.get("items", []):
        start = event["start"].get("dateTime", event["start"].get("date", ""))
        end = event["end"].get("dateTime", event["end"].get("date", ""))
        events.append(
            {
                "id": event["id"],
                "title": event.get("summary", "Sin título"),
                "start": start,
                "end": end,
                "description": event.get("description", ""),
            }
        )
    return events


def delete_calendar_event(event_id: str) -> str:
    """Delete a Google Calendar event by its ID."""
    service = get_google_service("calendar", "v3")
    service.events().delete(calendarId="primary", eventId=event_id).execute()
    return f"✅ Evento eliminado del calendario."


def block_calendar_time(
    title: str,
    start_time: str,
    end_time: str,
    branch: str,
    notes: str = "",
) -> str:
    """Create a focused-work time block in Google Calendar."""
    service = get_google_service("calendar", "v3")

    event = {
        "summary": f"[{branch}] {title}",
        "description": f"Rama: {branch}\n{notes}".strip(),
        "start": {"dateTime": start_time, "timeZone": TIMEZONE},
        "end": {"dateTime": end_time, "timeZone": TIMEZONE},
        "colorId": BRANCH_COLORS.get(branch, "1"),
    }

    created = service.events().insert(calendarId="primary", body=event).execute()
    return (
        f"✅ Bloque '{title}' creado en Google Calendar\n"
        f"   {start_time} → {end_time} | Rama: {branch}"
    )
