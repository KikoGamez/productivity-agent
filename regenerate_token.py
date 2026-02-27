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
    "https://www.googleapis.com/auth/spreadsheets",
]

# Try GOOGLE_CREDENTIALS_B64 from env, otherwise look for credentials.json
creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
if creds_b64:
    creds_data = json.loads(base64.b64decode(creds_b64))
    with open("_tmp_credentials.json", "w") as f:
        json.dump(creds_data, f)
    tmp_file = "_tmp_credentials.json"
elif os.path.exists("credentials.json"):
    tmp_file = "credentials.json"
else:
    print("❌ No se encontró GOOGLE_CREDENTIALS_B64 en .env ni credentials.json en este directorio.")
    print("   Descarga credentials.json desde Google Cloud Console y ponlo aquí.")
    exit(1)

flow = InstalledAppFlow.from_client_secrets_file(tmp_file, SCOPES)
creds = flow.run_local_server(port=0)

if tmp_file == "_tmp_credentials.json":
    os.remove(tmp_file)

print("\n✅ Nuevo GOOGLE_REFRESH_TOKEN:")
print(creds.refresh_token)

# Copy to clipboard automatically on Mac
try:
    import subprocess
    subprocess.run("pbcopy", input=creds.refresh_token.encode(), check=True)
    print("\n✅ Token copiado al portapapeles. Pégalo directamente en Railway.")
except Exception:
    print("\n👉 Copia el token de arriba y pégalo en Railway.")
