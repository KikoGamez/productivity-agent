import io
import os
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

from agent import TOOLS, execute_tool, _build_system_prompt
from tools.rag import get_relevant_context

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
client = anthropic.Anthropic()
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))

# Conversation history per chat_id
conversations: dict = {}

# ─────────────────────────────────────────────
# Prompts para briefings automáticos
# ─────────────────────────────────────────────

DAILY_BRIEFING_PROMPT = """Genera el briefing diario completo. Sé directo y estructurado. Usa emojis para separar secciones.

Pasos que debes seguir (usa las herramientas en este orden):
1. Llama a generate_agenda_data para obtener agenda, tareas pendientes y déficit de horas
2. Llama a web_search con la query: "most important business economy news today Financial Times Wall Street Journal Expansion site:ft.com OR site:wsj.com OR site:expansion.com OR site:economist.com OR site:nytimes.com" para obtener las 3 noticias más relevantes de prensa económica
3. Llama a read_emails (unread_only=true, max_emails=20) para revisar newsletters y emails importantes

Con todos los datos, genera el briefing con estas secciones:

📅 AGENDA DE HOY
Eventos del día y bloques de trabajo recomendados priorizando las ramas con más déficit de horas.

📰 TOP 3 NOTICIAS
Las 3 noticias más importantes del día de prensa económica y de negocios. Criterio: noticias con impacto real en economía, empresas, mercados o política económica — no lanzamientos de modelos de IA ni productos tech puros salvo que tengan impacto económico relevante (ej: movimiento bursátil, regulación, fusión, política comercial).
Por cada noticia: titular, una frase de contexto, y el link directo al medio que la haya cubierto mejor (FT, WSJ, The Economist, NYT Business, Bloomberg, Reuters, Expansión, El País Economía).

📧 EMAILS IMPORTANTES
REGLA ESTRICTA: solo aparecen aquí emails donde el remitente es una persona humana concreta (nombre + apellido o empresa conocida del usuario) que espera respuesta directa.
EXCLUIR SIN EXCEPCIONES aunque el contenido parezca urgente o accionable: notificaciones de GitHub, Google, LinkedIn, Notion, Stripe, Slack, cualquier plataforma o servicio, newsletters, alertas de seguridad automáticas, avisos de expiración de tokens/contraseñas, confirmaciones, facturas, marketing, y cualquier email enviado por un sistema automático o noreply.
Si no hay emails de personas reales: escribe únicamente "Nada urgente."

⚠️ RECORDATORIOS
- Ramas con déficit alto de horas esta semana
- Tareas que lleven mucho tiempo sin moverse (basándote en las tareas pendientes)
- Cualquier urgencia que detectes

💡 FOCO DEL DÍA
Una sola acción concreta que más impacto tendría hoy."""

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


async def _process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    """Core agentic loop — processes any text message (typed or transcribed)."""
    chat_id = update.effective_chat.id

    if chat_id not in conversations:
        conversations[chat_id] = []

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


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        await update.message.reply_text("⚠️ GROQ_API_KEY no configurada. No puedo transcribir audios.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Download voice file
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        audio_bytes = await voice_file.download_as_bytearray()

        # Transcribe with Groq Whisper
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

        # Show transcription and process
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
        print(f"⏰ Briefing diario: 07:00 Madrid | Resumen semanal: viernes 18:00 Madrid")
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
