"""
Run this script locally to regenerate GOOGLE_REFRESH_TOKEN with Sheets scope included.
Result: prints the new refresh token to paste in Railway.
"""
import os
import json
import base64
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
creds_data = json.loads(base64.b64decode(creds_b64))

# Save temporarily to a file (required by the OAuth flow library)
with open("_tmp_credentials.json", "w") as f:
    json.dump(creds_data, f)

flow = InstalledAppFlow.from_client_secrets_file("_tmp_credentials.json", SCOPES)
creds = flow.run_local_server(port=0)

os.remove("_tmp_credentials.json")

print("\n✅ Nuevo GOOGLE_REFRESH_TOKEN:")
print(creds.refresh_token)
print("\n👉 Actualiza esta variable en Railway con este valor.")
