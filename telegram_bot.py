import io
import os
import json
import asyncio
import datetime
import anthropic
from groq import Groq
from pypdf import PdfReader
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from dotenv import load_dotenv

try:
    from zoneinfo import ZoneInfo
    MADRID_TZ = ZoneInfo("Europe/Madrid")
except Exception:
    MADRID_TZ = datetime.timezone(datetime.timedelta(hours=1))

from agent import TOOLS, execute_tool, _build_system_prompt, get_memory_cached, invalidate_memory_cache
from tools.memory_tools import update_memory
from tools.rag import get_relevant_context

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
client = anthropic.Anthropic()
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))

# ─────────────────────────────────────────────
# Conversation persistence
# ─────────────────────────────────────────────

CONVERSATIONS_FILE = "conversations.json"
MAX_MESSAGES = 60  # max messages per chat (keep last N to avoid context overflow)


def _load_conversations() -> dict:
    try:
        with open(CONVERSATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_conversations(convs: dict):
    try:
        with open(CONVERSATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(convs, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error guardando conversaciones: {e}")


# Load from disk on startup
conversations: dict = _load_conversations()

# ─────────────────────────────────────────────
# Conversation sanitization
# ─────────────────────────────────────────────

def _sanitize_messages(messages: list) -> list:
    """Remove orphaned tool_use blocks that have no matching tool_result.
    This prevents 400 errors when conversation history gets corrupted mid-tool-use."""
    if not messages:
        return messages

    sanitized = list(messages)

    # Walk backwards and remove any trailing assistant message with unmatched tool_use
    while sanitized:
        last = sanitized[-1]
        if last["role"] == "assistant":
            content = last["content"]
            if isinstance(content, list):
                has_tool_use = any(
                    b.get("type") == "tool_use" if isinstance(b, dict) else getattr(b, "type", None) == "tool_use"
                    for b in content
                )
                if has_tool_use:
                    sanitized.pop()
                    continue
        break

    return sanitized


# ─────────────────────────────────────────────
# Prompts para briefings automáticos
# ─────────────────────────────────────────────

DAILY_BRIEFING_PROMPT = """Genera el briefing diario completo. Sé directo y estructurado. Usa emojis para separar secciones.

Pasos que debes seguir EN ESTE ORDEN:
1. Llama a web_search con la query: "AI robotics tech companies business news today site:ft.com OR site:nytimes.com OR site:wsj.com OR site:bloomberg.com OR site:reuters.com" — noticias de negocio sobre empresas tecnológicas, IA y robótica
2. Llama a read_emails (yesterday_only=true, unread_only=false, max_emails=30) para revisar emails del día anterior
3. Llama a generate_agenda_data para obtener eventos del día, tareas pendientes, horas consumidas por rama y déficit de horas

Con todos los datos, genera el briefing con estas secciones EN ESTE ORDEN:

📰 TOP 3 NOTICIAS
Noticias de negocio sobre empresas de IA, robótica y tecnología: financiación, adquisiciones, lanzamientos de producto con impacto comercial, movimientos de mercado, regulación, alianzas estratégicas. Cubiertas por FT, NYT, WSJ, Bloomberg o Reuters.
EXCLUIR: análisis macroeconómicos generales sin empresa tech protagonista, noticias de política o economía sin conexión directa con el sector tech.
Por cada noticia: titular, una frase de contexto y link directo al medio que la haya cubierto mejor.

📧 EMAILS A RESPONDER
REGLA ESTRICTA: solo emails de personas reales que esperan respuesta directa tuya.
Excluir sin excepciones: newsletters, marketing, notificaciones automáticas, noreply, alertas de plataformas, confirmaciones, facturas.
Excepción LinkedIn: incluir SOLO notificaciones de mensaje directo recibido ("tienes un nuevo mensaje de X"). Excluir todo lo demás de LinkedIn (visitas al perfil, likes, sugerencias, invitaciones, etc.).
Si no hay ninguno: "Nada urgente."

📅 AGENDA PROPUESTA
Con todos los datos anteriores (eventos, tareas pendientes, horas consumidas por rama, déficit acumulado y emails a responder), propón un plan concreto para el día con bloques horarios aproximados. Prioriza las ramas con más déficit de horas y los emails que requieren respuesta. Menciona el estado de horas de cada rama si hay déficit relevante.

⚠️ RECORDATORIOS
Ramas con déficit alto esta semana, tareas que lleven mucho tiempo sin moverse, cualquier urgencia detectada."""

WEEKLY_SUMMARY_PROMPT = """Genera el resumen semanal de productividad. Sé directo y estructurado.

Pasos que debes seguir:
1. Llama a generate_agenda_data para obtener horas trabajadas esta semana, déficit por rama y tareas
2. Llama a get_tasks con status=Done para ver qué se ha completado

Con los datos, genera el resumen con estas secciones:

📊 HORAS ESTA SEMANA
Horas reales vs objetivo por rama. Porcentaje de cumplimiento. Total semanal.

✅ COMPLETADO ESTA SEMANA
Tareas cerradas. Si no hay datos claros, menciona los avances más relevantes detectados.

🔴 DÉFICIT ACUMULADO
Las ramas con más horas por recuperar y qué significa para la próxima semana.

📋 PRIORIDADES PRÓXIMA SEMANA
Top 5 tareas más importantes para la próxima semana basándote en las pendientes.

🏆 HIGHLIGHT
El logro más destacable de la semana o una reflexión útil sobre el ritmo de trabajo."""


# ─────────────────────────────────────────────
# Agent loop reutilizable para briefings
# ─────────────────────────────────────────────

async def _run_briefing(context: ContextTypes.DEFAULT_TYPE, prompt: str, header: str, chat_id: str = None):
    """Run the full agent loop for a briefing and send the result.
    chat_id: explicit target (manual commands pass update.effective_chat.id).
             Falls back to TELEGRAM_CHAT_ID for scheduled jobs.
    """
    target = str(chat_id) if chat_id else TELEGRAM_CHAT_ID
    if not target:
        print("⚠️ TELEGRAM_CHAT_ID no configurado — briefing automático desactivado.")
        return

    await context.bot.send_message(chat_id=target, text=header)
    await context.bot.send_chat_action(chat_id=target, action="typing")

    rag_context = await asyncio.to_thread(get_relevant_context, prompt)
    system_prompt = _build_system_prompt(extra_context=rag_context)
    messages = [{"role": "user", "content": prompt}]

    try:
        while True:
            for attempt in range(3):
                try:
                    response = await asyncio.to_thread(
                        client.messages.create,
                        model="claude-sonnet-4-6",
                        max_tokens=4096,
                        system=system_prompt,
                        tools=TOOLS,
                        messages=messages,
                    )
                    break
                except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
                    is_overloaded = isinstance(e, anthropic.APIStatusError) and e.status_code == 529
                    if attempt == 2 or not (isinstance(e, anthropic.RateLimitError) or is_overloaded):
                        raise
                    await asyncio.sleep(30)
                    await context.bot.send_chat_action(chat_id=target, action="typing")

            if response.stop_reason == "end_turn":
                messages.append({"role": "assistant", "content": response.content})
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        text = block.text
                        while text:
                            await context.bot.send_message(chat_id=target, text=text[:4096])
                            text = text[4096:]
                break

            elif response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        await context.bot.send_chat_action(chat_id=target, action="typing")
                        result = await asyncio.to_thread(execute_tool, block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})

            else:
                messages.append({"role": "assistant", "content": response.content})
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        await context.bot.send_message(chat_id=target, text=block.text[:4096])
                break

    except Exception as exc:
        await context.bot.send_message(chat_id=target, text=f"⚠️ Error generando briefing: {exc}")


# ─────────────────────────────────────────────
# Scheduled jobs
# ─────────────────────────────────────────────

async def daily_briefing_job(context: ContextTypes.DEFAULT_TYPE):
    await _run_briefing(
        context,
        prompt=DAILY_BRIEFING_PROMPT,
        header="🌅 Buenos días — aquí tu briefing diario:",
    )


async def weekly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    await _run_briefing(
        context,
        prompt=WEEKLY_SUMMARY_PROMPT,
        header="📊 Resumen semanal — esto es lo que ha pasado esta semana:",
    )


async def google_token_keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    """Refresh Google token every 5 days to prevent expiry in Testing mode."""
    try:
        from tools.google_auth import get_credentials
        await asyncio.to_thread(get_credentials)
        print("🔑 Google token keep-alive: OK")
    except Exception as e:
        print(f"⚠️ Google token keep-alive falló: {e}")
        if TELEGRAM_CHAT_ID:
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"⚠️ El token de Google necesita renovarse. Ejecuta `python regenerate_token.py` y actualiza Railway.\n\nError: {e}"
            )


# ─────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    notion_token = os.environ.get("NOTION_TOKEN", "")
    telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
    groq_key = os.environ.get("GROQ_API_KEY", "")
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")

    msg = (
        f"🔍 Debug:\n"
        f"ANTHROPIC_API_KEY: {'✅' if anthropic_key else '❌'} ({len(anthropic_key)} chars)\n"
        f"NOTION_TOKEN: {'✅' if notion_token else '❌'} ({len(notion_token)} chars)\n"
        f"TELEGRAM_TOKEN: {'✅' if telegram_token else '❌'} ({len(telegram_token)} chars)\n"
        f"TELEGRAM_CHAT_ID: {'✅' if TELEGRAM_CHAT_ID else '❌ NO CONFIGURADO'}\n"
        f"GOOGLE_CREDENTIALS_B64: {'✅' if creds_b64 else '❌'} ({len(creds_b64)} chars)\n"
        f"GOOGLE_REFRESH_TOKEN: {'✅' if refresh_token else '❌'} ({len(refresh_token)} chars)\n"
        f"GROQ_API_KEY: {'✅' if groq_key else '❌'} ({len(groq_key)} chars)\n"
        f"PERPLEXITY_API_KEY: {'✅' if perplexity_key else '❌'} ({len(perplexity_key)} chars)\n"
    )

    try:
        from tools.google_auth import get_google_service
        get_google_service("calendar", "v3")
        msg += "Google Calendar: ✅\n"
    except Exception as e:
        msg += f"Google Calendar: ❌ {e}\n"

    await update.message.reply_text(msg)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"Tu Chat ID es: {chat_id}\n\n"
        f"Añade esta variable en Railway para activar los mensajes automáticos:\n"
        f"TELEGRAM_CHAT_ID = {chat_id}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola, soy tu agente de productividad personal.\n\n"
        "Puedo ayudarte a:\n"
        "• 📋 Gestionar tareas en Notion\n"
        "• 📅 Ver y bloquear tiempo en Google Calendar\n"
        "• 📧 Leer y analizar tus correos\n"
        "• 📝 Guardar notas de reuniones\n"
        "• 🗓️ Generar tu agenda del día\n"
        "• 🎤 Mandarme audios de voz\n"
        "• 🔍 Buscar noticias e información en internet\n\n"
        "Comandos disponibles:\n"
        "• /briefing — briefing diario ahora\n"
        "• /resumen — resumen semanal ahora\n"
        "• /myid — ver tu Chat ID\n\n"
        "¿En qué te ayudo?"
    )


async def manual_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _run_briefing(context, DAILY_BRIEFING_PROMPT, "🌅 Briefing diario:", chat_id=update.effective_chat.id)


async def manual_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _run_briefing(context, WEEKLY_SUMMARY_PROMPT, "📊 Resumen semanal:", chat_id=update.effective_chat.id)


async def _consolidate_memory(chat_id: str):
    """Background task: after each exchange, extract learnings and update memory via Haiku."""
    messages = conversations.get(chat_id, [])
    # Only text turns (skip tool_use/tool_result blocks)
    text_turns = [
        m for m in messages[-20:]
        if isinstance(m.get("content"), str) and m["content"].strip()
    ]
    if len(text_turns) < 2:
        return

    conversation_text = "\n".join(
        f"{'USUARIO' if m['role'] == 'user' else 'AGENTE'}: {m['content']}"
        for m in text_turns
    )
    current_memory = get_memory_cached()

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-haiku-4-5",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"""Eres el sistema de memoria de un asistente personal. Analiza la conversación y actualiza la memoria del usuario.

MEMORIA ACTUAL:
{current_memory or "(vacía)"}

CONVERSACIÓN RECIENTE:
{conversation_text}

Extrae TODO lo que sea útil para conocer mejor al usuario: trabajo, proyectos, decisiones, preferencias, hábitos, contactos mencionados, situación personal, frustraciones, objetivos, contexto de lo que está haciendo.
Sé agresivo guardando información — mejor guardar de más que de menos.
Integra lo nuevo con la memoria existente sin borrar nada relevante.
Organiza en secciones: ## Trabajo y proyectos / ## Preferencias y hábitos / ## Contactos clave / ## Situación actual / ## Contexto y patrones.

Si realmente no hay nada nuevo relevante que añadir, responde solo: NO_UPDATE
Si hay algo (aunque sea pequeño), responde con la memoria completa actualizada.""",
            }],
        )
        result = response.content[0].text.strip()
        if result != "NO_UPDATE" and len(result) > 100:
            await asyncio.to_thread(update_memory, result)
            invalidate_memory_cache()
    except Exception as e:
        print(f"⚠️ Error consolidando memoria: {e}")


async def _process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    """Core agentic loop — processes any text message (typed or transcribed)."""
    chat_id = str(update.effective_chat.id)

    if chat_id not in conversations:
        conversations[chat_id] = []

    # Clean any orphaned tool_use blocks before adding new message
    conversations[chat_id] = _sanitize_messages(conversations[chat_id])
    conversations[chat_id].append({"role": "user", "content": user_message})
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # RAG: auto-inject relevant documents into the system prompt
    rag_context = await asyncio.to_thread(get_relevant_context, user_message)
    system_prompt = _build_system_prompt(extra_context=rag_context)
    messages = conversations[chat_id]

    try:
        while True:
            for attempt in range(3):
                try:
                    response = await asyncio.to_thread(
                        client.messages.create,
                        model="claude-sonnet-4-6",
                        max_tokens=2048,
                        system=system_prompt,
                        tools=TOOLS,
                        messages=messages,
                    )
                    break
                except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
                    is_overloaded = isinstance(e, anthropic.APIStatusError) and e.status_code == 529
                    if attempt == 2 or not (isinstance(e, anthropic.RateLimitError) or is_overloaded):
                        raise
                    await asyncio.sleep(30)
                    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            if response.stop_reason == "end_turn":
                messages.append({"role": "assistant", "content": response.content})
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        text = block.text
                        while text:
                            await update.message.reply_text(text[:4096])
                            text = text[4096:]
                break

            elif response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                        result = await asyncio.to_thread(execute_tool, block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "user", "content": tool_results})

            else:
                messages.append({"role": "assistant", "content": response.content})
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        await update.message.reply_text(block.text[:4096])
                break

    except Exception as exc:
        await update.message.reply_text(f"⚠️ Error: {exc}")
    finally:
        # Trim to last MAX_MESSAGES and persist to disk
        if len(conversations[chat_id]) > MAX_MESSAGES:
            conversations[chat_id] = conversations[chat_id][-MAX_MESSAGES:]
        _save_conversations(conversations)
        # Fire-and-forget memory consolidation (doesn't block the response)
        asyncio.create_task(_consolidate_memory(chat_id))


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conversations[chat_id] = []
    _save_conversations(conversations)
    await update.message.reply_text("🗑️ Historial borrado. Empezamos de cero.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _process_message(update, context, update.message.text)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    mime = doc.mime_type or ""

    # Telegram bots can only download files up to 20MB
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text(
            "⚠️ El archivo es demasiado grande (máx. 20MB). "
            "Prueba a dividir el PDF en partes más pequeñas."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_document")

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()
        title = doc.file_name or "Documento sin nombre"

        if mime == "application/pdf":
            reader = PdfReader(io.BytesIO(bytes(file_bytes)))
            pages_text = [page.extract_text() or "" for page in reader.pages]
            content = "\n".join(pages_text).strip()
            if not content:
                await update.message.reply_text("⚠️ No pude extraer texto de este PDF (puede ser una imagen escaneada).")
                return
        elif mime.startswith("text/"):
            content = bytes(file_bytes).decode("utf-8", errors="replace")
        else:
            await update.message.reply_text(f"⚠️ Formato no soportado ({mime}). Envíame un PDF o archivo de texto.")
            return

        from tools.documents_tools import save_document
        result = await asyncio.to_thread(
            save_document,
            title=title.rsplit(".", 1)[0],  # Remove extension from title
            content=content,
            source="Manual",
        )
        await update.message.reply_text(f"📄 {result}\n\nPuedes pedirme que lo busque o lo resuma cuando quieras.")

    except Exception as exc:
        await update.message.reply_text(f"⚠️ Error procesando el archivo: {exc}")


def _clean_transcription(text: str) -> str:
    """Post-process a raw Whisper transcription with Haiku:
    add punctuation, fix obvious transcription errors, preserve all words."""
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                "Eres un corrector de transcripciones de voz. "
                "Tu única tarea es añadir puntuación, mayúsculas y corregir palabras que el reconocedor de voz haya transcrito mal (por similitud fonética). "
                "NUNCA cambies el significado, añadas palabras nuevas ni elimines nada. "
                "Devuelve ÚNICAMENTE el texto corregido, sin explicaciones ni comentarios.\n\n"
                f"Transcripción:\n{text}"
            ),
        }],
    )
    return response.content[0].text.strip()


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        await update.message.reply_text("⚠️ GROQ_API_KEY no configurada. No puedo transcribir audios.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        audio_bytes = await voice_file.download_as_bytearray()

        transcription = await asyncio.to_thread(
            groq_client.audio.transcriptions.create,
            file=("audio.ogg", bytes(audio_bytes)),
            model="whisper-large-v3",
            language="es",
        )
        text = transcription.text.strip()

        if not text:
            await update.message.reply_text("⚠️ No pude entender el audio.")
            return

        # Post-process: fix punctuation and transcription errors without changing content
        text = await asyncio.to_thread(_clean_transcription, text)

        # If starts with a transcription keyword, strip it and return plain text only
        lower = text.lower()
        transcribe_prefixes = ["transcríbeme esto", "transcribeme esto",
                               "transcríbeme", "transcribeme",
                               "transcripción", "transcripcion"]
        is_transcribe_only = False
        for prefix in transcribe_prefixes:
            if lower.startswith(prefix):
                text = text[len(prefix):].lstrip(" .,;:—-")
                is_transcribe_only = True
                break
        if is_transcribe_only:
            await update.message.reply_text(text)
        else:
            # Process as a regular message through the agent
            await update.message.reply_text(f"🎤 _{text}_", parse_mode="Markdown")
            await _process_message(update, context, text)

    except Exception as exc:
        await update.message.reply_text(f"⚠️ Error transcribiendo audio: {exc}")


def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN no está en .env")
        return

    import time
    print("⏳ Esperando 8s para que Telegram libere la conexión anterior...")
    time.sleep(8)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("briefing", manual_briefing))
    app.add_handler(CommandHandler("resumen", manual_weekly))
    app.add_handler(CommandHandler("olvida", clear_history))

    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Scheduled jobs
    if TELEGRAM_CHAT_ID:
        job_queue = app.job_queue
        # Daily briefing at 7:00 AM Madrid time
        job_queue.run_daily(
            daily_briefing_job,
            time=datetime.time(7, 0, 0, tzinfo=MADRID_TZ),
        )
        # Weekly summary every Friday at 6:00 PM Madrid time
        job_queue.run_daily(
            weekly_summary_job,
            time=datetime.time(18, 0, 0, tzinfo=MADRID_TZ),
            days=(4,),  # 0=Monday … 4=Friday
        )
        # Google token keep-alive every 5 days
        job_queue.run_repeating(
            google_token_keepalive_job,
            interval=datetime.timedelta(days=5),
            first=datetime.timedelta(seconds=10),
        )
        print(f"⏰ Briefing diario: 07:00 Madrid | Resumen semanal: viernes 18:00 Madrid | Token keep-alive: cada 5 días")
    else:
        print("⚠️ TELEGRAM_CHAT_ID no configurado — mensajes automáticos desactivados. Usa /myid para obtenerlo.")

    google_vars = [k for k in os.environ if k.startswith("GOOGLE")]
    print(f"🔑 Variables GOOGLE detectadas: {google_vars}")
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
    print(f"🔍 PERPLEXITY_API_KEY: {'✅' if perplexity_key else '❌ NO CONFIGURADA'}")
    print("🤖 Bot de Telegram iniciado. Escribe /start en Telegram.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
