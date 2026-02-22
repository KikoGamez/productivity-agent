import os
import json
import base64
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def get_credentials() -> Credentials:
    """Build Google OAuth2 credentials from environment variables."""
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    if not refresh_token:
        raise RuntimeError(
            "GOOGLE_REFRESH_TOKEN no está configurado en las variables de entorno."
        )

    # Read client_id and client_secret from GOOGLE_CREDENTIALS_B64
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
    if not creds_b64:
        raise RuntimeError(
            "GOOGLE_CREDENTIALS_B64 no está configurado en las variables de entorno."
        )

    creds_data = json.loads(base64.b64decode(creds_b64))
    client_info = creds_data.get("web") or creds_data.get("installed", {})
    client_id = client_info["client_id"]
    client_secret = client_info["client_secret"]
    token_uri = client_info.get("token_uri", "https://oauth2.googleapis.com/token")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def get_google_service(service_name: str, version: str):
    """Build and return an authenticated Google API service."""
    creds = get_credentials()
    return build(service_name, version, credentials=creds)
