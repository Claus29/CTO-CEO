# ruff: noqa: E402  (el TOKEN va arriba del todo a propósito)
"""
Bot conector entre dos personas (Telegram).

- Solo existe /start. Las dos primeras personas que lo pulsen quedan ancladas
  al bot para siempre (se guarda en un archivo JSON junto a este script).
- Todo lo que escribe una persona le llega a la otra: texto, fotos, vídeos,
  audios, notas de voz, videonotas, stickers, GIFs, documentos, ubicaciones,
  contactos, encuestas...
- Si respondes (reply) a un mensaje, la respuesta se mantiene en el otro lado.
- Cualquier otra persona que entre recibe un aviso de que el bot está ocupado.

Requisitos:
    pip install "python-telegram-bot>=21,<23"

Uso:
    1. Pon tu token en TOKEN (te lo da @BotFather).
    2. python bot_conector.py
"""

# =====================================================================
TOKEN = "PON_AQUI_TU_TOKEN"
# =====================================================================

import json
import logging
from pathlib import Path

from telegram import ReplyParameters, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

ARCHIVO_PAREJA = Path(__file__).with_name("pareja.json")
MAX_ENLACES_RESPUESTA = 5000  # cuántos mensajes recuerda para mantener las respuestas

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("conector")


def cargar_pareja() -> list[int]:
    try:
        return [int(x) for x in json.loads(ARCHIVO_PAREJA.read_text(encoding="utf-8"))][:2]
    except (FileNotFoundError, ValueError, TypeError):
        return []


def guardar_pareja(ids: list[int]) -> None:
    ARCHIVO_PAREJA.write_text(json.dumps(ids), encoding="utf-8")


pareja: list[int] = cargar_pareja()
# (chat_id, message_id) -> message_id equivalente en el chat de la otra persona
enlaces: dict[tuple[int, int], int] = {}


def otra_persona(user_id: int) -> int | None:
    if len(pareja) == 2 and user_id in pareja:
        return pareja[1] if pareja[0] == user_id else pareja[0]
    return None


def enlazar(chat_a: int, msg_a: int, chat_b: int, msg_b: int) -> None:
    enlaces[(chat_a, msg_a)] = msg_b
    enlaces[(chat_b, msg_b)] = msg_a
    while len(enlaces) > MAX_ENLACES_RESPUESTA * 2:
        enlaces.pop(next(iter(enlaces)))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if user_id in pareja:
        if len(pareja) == 2:
            await update.message.reply_text("✅ Ya estás conectado. Escribe y le llegará a la otra persona.")
        else:
            await update.message.reply_text("⏳ Esperando a que entre la otra persona...")
        return

    if len(pareja) >= 2:
        await update.message.reply_text("🔒 Este bot es privado y ya está ocupado.")
        return

    pareja.append(user_id)
    guardar_pareja(pareja)
    log.info("Persona %s anclada (%s/2)", user_id, len(pareja))

    if len(pareja) == 1:
        await update.message.reply_text("⏳ Estás dentro. Esperando a que entre la otra persona...")
    else:
        await update.message.reply_text("✅ Conectado. Todo lo que mandes aquí le llegará a la otra persona.")
        try:
            await context.bot.send_message(pareja[0], "✅ La otra persona ya ha entrado. ¡Ya podéis hablar!")
        except TelegramError as e:
            log.warning("No se pudo avisar a %s: %s", pareja[0], e)


async def reenviar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if msg is None:
        return

    user_id = update.effective_user.id
    destino = otra_persona(user_id)

    if destino is None:
        if user_id in pareja:
            await msg.reply_text("⏳ Todavía no ha entrado la otra persona.")
        elif len(pareja) >= 2:
            await msg.reply_text("🔒 Este bot es privado y ya está ocupado.")
        else:
            await msg.reply_text("Pulsa /start para entrar.")
        return

    responder_a = None
    if msg.reply_to_message:
        responder_a = enlaces.get((msg.chat_id, msg.reply_to_message.message_id))

    try:
        copia = await context.bot.copy_message(
            chat_id=destino,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
            reply_parameters=(
                ReplyParameters(message_id=responder_a, allow_sending_without_reply=True) if responder_a else None
            ),
        )
        enlazar(msg.chat_id, msg.message_id, destino, copia.message_id)
    except TelegramError as e:
        log.warning("No se pudo entregar el mensaje: %s", e)
        await msg.reply_text("⚠️ No se pudo entregar este mensaje.")


def main() -> None:
    if not TOKEN or TOKEN == "PON_AQUI_TU_TOKEN":
        raise SystemExit("Pon tu token en la variable TOKEN al principio del archivo.")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.StatusUpdate.ALL, reenviar))

    log.info("Bot en marcha. Pareja actual: %s", pareja or "nadie todavía")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
