import os
from datetime import datetime, timedelta
from notion_client import Client

notion = Client(auth=os.environ.get("NOTION_TOKEN", ""))

TASKS_DB_ID = os.environ.get("NOTION_TASKS_DB_ID", "")
NOTES_DB_ID = os.environ.get("NOTION_NOTES_DB_ID", "")
TIME_LOG_DB_ID = os.environ.get("NOTION_TIME_LOG_DB_ID", "")


def _get_text(prop) -> str:
    """Safely extract text from a Notion rich_text or title property."""
    if not prop:
        return ""
    items = prop.get("rich_text") or prop.get("title") or []
    return "".join(item.get("plain_text", "") for item in items)


def create_task(
    title: str,
    branch: str,
    priority: str,
    estimated_hours: float,
    due_date: str = None,
    notes: str = "",
) -> str:
    """Create a task in the Notion Tasks database."""
    properties = {
        "Name": {"title": [{"text": {"content": title}}]},
        "Branch": {"select": {"name": branch}},
        "Status": {"select": {"name": "Pending"}},
        "Priority": {"select": {"name": priority}},
        "Estimated Hours": {"number": estimated_hours},
    }
    if due_date:
        properties["Due Date"] = {"date": {"start": due_date}}
    if notes:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    notion.pages.create(
        parent={"database_id": TASKS_DB_ID},
        properties=properties,
    )
    return f"✅ Tarea '{title}' creada en '{branch}' (prioridad: {priority}, ~{estimated_hours}h)"


def get_tasks(branch: str = None, status: str = None) -> list:
    """Query tasks from Notion, optionally filtered by branch and/or status."""
    filters = []
    if branch:
        filters.append({"property": "Branch", "select": {"equals": branch}})
    if status:
        filters.append({"property": "Status", "select": {"equals": status}})

    query_params = {
        "database_id": TASKS_DB_ID,
        "page_size": 100,
        "sorts": [
            {"property": "Priority", "direction": "descending"},
            {"property": "Due Date", "direction": "ascending"},
        ],
    }
    if filters:
        query_params["filter"] = {"and": filters} if len(filters) > 1 else filters[0]

    results = notion.databases.query(**query_params)
    tasks = []
    for page in results.get("results", []):
        props = page["properties"]
        task = {
            "id": page["id"],
            "title": _get_text(props.get("Name")),
            "branch": (props.get("Branch") or {}).get("select", {}).get("name", ""),
            "status": (props.get("Status") or {}).get("select", {}).get("name", ""),
            "priority": (props.get("Priority") or {}).get("select", {}).get("name", ""),
            "estimated_hours": (props.get("Estimated Hours") or {}).get("number", 0),
        }
        due = (props.get("Due Date") or {}).get("date")
        if due:
            task["due_date"] = due.get("start", "")
        tasks.append(task)
    return tasks


def save_meeting_notes(
    title: str,
    attendees: str,
    notes: str,
    action_items: str = "",
) -> str:
    """Save meeting notes to the Notion Meeting Notes database."""
    properties = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Date": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
        "Attendees": {"rich_text": [{"text": {"content": attendees}}]},
        "Notes": {"rich_text": [{"text": {"content": notes[:2000]}}]},
    }
    if action_items:
        properties["Action Items"] = {
            "rich_text": [{"text": {"content": action_items}}]
        }

    notion.pages.create(
        parent={"database_id": NOTES_DB_ID},
        properties=properties,
    )
    return f"✅ Notas de '{title}' guardadas en Notion"


def get_weekly_hours_by_branch() -> dict:
    """Return hours logged per branch for the current ISO week."""
    monday = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime(
        "%Y-%m-%d"
    )
    results = notion.databases.query(
        database_id=TIME_LOG_DB_ID,
        filter={"property": "Date", "date": {"on_or_after": monday}},
        page_size=100,
    )
    hours_by_branch: dict = {}
    for page in results.get("results", []):
        props = page["properties"]
        branch = (props.get("Branch") or {}).get("select", {}).get("name")
        hours = (props.get("Hours") or {}).get("number", 0) or 0
        if branch:
            hours_by_branch[branch] = round(hours_by_branch.get(branch, 0) + hours, 2)
    return hours_by_branch


def log_time(branch: str, hours: float, task_description: str = "") -> str:
    """Log hours worked on a branch into the Time Log database."""
    description = task_description or f"Trabajo en {branch}"
    properties = {
        "Task": {"title": [{"text": {"content": description}}]},
        "Branch": {"select": {"name": branch}},
        "Date": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
        "Hours": {"number": hours},
    }
    notion.pages.create(
        parent={"database_id": TIME_LOG_DB_ID},
        properties=properties,
    )
    return f"✅ {hours}h registradas en '{branch}'"
