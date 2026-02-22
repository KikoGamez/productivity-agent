import os
import sys
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "NOTION_TOKEN",
    "NOTION_TASKS_DB_ID",
    "NOTION_NOTES_DB_ID",
    "NOTION_TIME_LOG_DB_ID",
]


def check_env():
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        print("❌ Faltan variables en .env:")
        for v in missing:
            print(f"   - {v}")
        print("\n→ Copia .env.example a .env y completa los valores.")
        print("→ Ejecuta 'python setup_notion.py' para crear las DBs de Notion.")
        sys.exit(1)


def check_google_credentials():
    if not os.path.exists("credentials.json"):
        print("❌ No se encontró credentials.json")
        print("\nPasos:")
        print("  1. Ve a console.cloud.google.com → APIs y servicios → Credenciales")
        print("  2. Crea credenciales OAuth 2.0 (tipo: Aplicación de escritorio)")
        print("  3. Activa las APIs: Gmail API y Google Calendar API")
        print("  4. Descarga el JSON y renómbralo 'credentials.json'")
        print("  5. Colócalo en la carpeta del proyecto")
        sys.exit(1)


if __name__ == "__main__":
    check_env()
    check_google_credentials()

    from agent import run_agent
    run_agent()
