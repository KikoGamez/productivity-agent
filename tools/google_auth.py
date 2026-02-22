import os
import base64
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
]

TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"


def _setup_files_from_env():
    """Write credentials files from env vars when running on a server."""
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
    token_b64 = os.environ.get("GOOGLE_TOKEN_B64")

    print(f"[Google Auth] GOOGLE_CREDENTIALS_B64 present: {bool(creds_b64)}, length: {len(creds_b64) if creds_b64 else 0}")
    print(f"[Google Auth] GOOGLE_TOKEN_B64 present: {bool(token_b64)}, length: {len(token_b64) if token_b64 else 0}")

    if creds_b64:
        with open(CREDENTIALS_PATH, "wb") as f:
            f.write(base64.b64decode(creds_b64))
        print(f"[Google Auth] credentials.json written")

    if token_b64:
        with open(TOKEN_PATH, "wb") as f:
            f.write(base64.b64decode(token_b64))
        print(f"[Google Auth] token.json written")


def get_credentials() -> Credentials:
    """Get or refresh Google OAuth2 credentials."""
    _setup_files_from_env()

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "Google auth: no hay token válido y no se puede abrir el navegador en el servidor. "
                "Asegúrate de que GOOGLE_TOKEN_B64 está correctamente configurado en las variables de entorno."
            )
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return creds


def get_google_service(service_name: str, version: str):
    """Build and return an authenticated Google API service."""
    creds = get_credentials()
    return build(service_name, version, credentials=creds)
