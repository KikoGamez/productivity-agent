import os
import urllib.request
from dotenv import load_dotenv

load_dotenv()
t = os.environ.get("NOTION_TOKEN", "")

req = urllib.request.Request(
    "https://api.notion.com/v1/users/me",
    headers={"Authorization": "Bearer " + t, "Notion-Version": "2022-06-28"},
)
try:
    res = urllib.request.urlopen(req)
    print("✅ Token válido, status:", res.status)
except urllib.error.HTTPError as e:
    print("❌ Error:", e.code, e.read().decode())
