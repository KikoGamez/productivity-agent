import os
from dotenv import load_dotenv
load_dotenv()

from notion_client import Client

notion = Client(auth=os.environ["NOTION_TOKEN"])
db_id = os.environ["NOTION_TASKS_DB_ID"]

try:
    result = notion.databases.query(database_id=db_id, page_size=1)
    print("✅ Notion OK, tareas encontradas:", len(result["results"]))
except Exception as e:
    print("❌ Error:", e)
