import os
import asyncio
import anthropic
from groq import Groq
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from dotenv import load_dotenv

from agent import TOOLS, execute_tool, _build_system_prompt
from tools.rag import get_relevant_context

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
client = anthropic.Anthropic()
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))

# Conversation history per chat_id
conversations: dict = {}


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    notion_token = os.environ.get("NOTION_TOKEN", "")
    telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
    groq_key = os.environ.get("GROQ_API_KEY", "")

    msg = (
        f"🔍 Debug:\n"
        f"ANTHROPIC_API_KEY: {'✅' if anthropic_key else '❌'} ({len(anthropic_key)} chars)\n"
        f"NOTION_TOKEN: {'✅' if notion_token else '❌'} ({len(notion_token)} chars)\n"
        f"TELEGRAM_TOKEN: {'✅' if telegram_token else '❌'} ({len(telegram_token)} chars)\n"
        f"GOOGLE_CREDENTIALS_B64: {'✅' if creds_b64 else '❌'} ({len(creds_b64)} chars)\n"
        f"GOOGLE_REFRESH_TOKEN: {'✅' if refresh_token else '❌'} ({len(refresh_token)} chars)\n"
        f"GROQ_API_KEY: {'✅' if groq_key else '❌'} ({len(groq_key)} chars)\n"
    )

    try:
        from tools.google_auth import get_google_service
        get_google_service("calendar", "v3")
        msg += "Google Calendar: ✅\n"
    except Exception as e:
        msg += f"Google Calendar: ❌ {e}\n"

    await update.message.reply_text(msg)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola, soy tu agente de productividad personal.\n\n"
        "Puedo ayudarte a:\n"
        "• 📋 Gestionar tareas en Notion\n"
        "• 📅 Ver y bloquear tiempo en Google Calendar\n"
        "• 📧 Leer y analizar tus correos\n"
        "• 📝 Guardar notas de reuniones\n"
        "• 🗓️ Generar tu agenda del día\n"
        "• 🎤 Mandarme audios de voz\n\n"
        "¿En qué te ayudo?"
    )


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

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    google_vars = [k for k in os.environ if k.startswith("GOOGLE")]
    print(f"🔑 Variables GOOGLE detectadas: {google_vars}")
    print("🤖 Bot de Telegram iniciado. Escribe /start en Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()
