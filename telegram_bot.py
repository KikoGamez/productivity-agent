import os
import asyncio
import anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from dotenv import load_dotenv

from agent import TOOLS, execute_tool, _build_system_prompt

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
client = anthropic.Anthropic()

# Conversation history per chat_id
conversations: dict = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola, soy tu agente de productividad personal.\n\n"
        "Puedo ayudarte a:\n"
        "• 📋 Gestionar tareas en Notion\n"
        "• 📅 Ver y bloquear tiempo en Google Calendar\n"
        "• 📧 Leer y analizar tus correos\n"
        "• 📝 Guardar notas de reuniones\n"
        "• 🗓️ Generar tu agenda del día\n\n"
        "¿En qué te ayudo?"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = update.message.text

    if chat_id not in conversations:
        conversations[chat_id] = []

    conversations[chat_id].append({"role": "user", "content": user_message})

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    system_prompt = _build_system_prompt()
    messages = conversations[chat_id]

    try:
        while True:
            response = await asyncio.to_thread(
                client.messages.create,
                model="claude-opus-4-6",
                max_tokens=4096,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive"},
            )

            if response.stop_reason == "end_turn":
                messages.append({"role": "assistant", "content": response.content})
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        # Telegram tiene límite de 4096 caracteres por mensaje
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


def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN no está en .env")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot de Telegram iniciado. Escribe /start en Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()
