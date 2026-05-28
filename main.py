import os
import json
import base64
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from generate_tf import create_session, write_tfvars, cleanup_session
from terraform_manager import run_terraform, is_capacity_error

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.DEBUG)
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("BOT_TOKEN is not set!")
    logging.error("Found: %s", [k for k in os.environ.keys() if "BOT" in k or "TOKEN" in k or "ALLOWED" in k])
    raise SystemExit("ERROR: BOT_TOKEN is required.")

ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))

CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"

def setup_creds_from_env():
    tenancy = os.getenv("TENANCY_OCID")
    user = os.getenv("USER_OCID")
    fingerprint = os.getenv("FINGERPRINT")
    pem_b64 = os.getenv("PEM_B64")

    if tenancy and user and fingerprint and pem_b64:
        logging.info("Loading credentials from environment variables")
        try:
            base64.b64decode(pem_b64)
        except Exception as e:
            logging.error("PEM_B64 is not valid base64: %s", e)
            return False

        creds = {
            "tenancy_ocid": tenancy,
            "user_ocid": user,
            "fingerprint": fingerprint,
            "pem_b64": pem_b64,
        }
        CREDENTIALS_FILE.write_text(json.dumps(creds, indent=2), encoding="utf-8")
        logging.info("credentials.json created")
        return True
    logging.warning("Missing env vars")
    return False

def load_creds():
    if not CREDENTIALS_FILE.exists():
        if not setup_creds_from_env():
            return {}
    return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))

def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != ALLOWED_USER_ID:
            await update.message.reply_text("No autorizado.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("No autorizado.")
        return
    await update.message.reply_text(
        "🤖 *Bot OCI Free Tier*\n\n"
        "Usá /create para crear una VM Ampere A1.\n"
        "Usá /cancel para detener un despliegue en curso.",
        parse_mode="Markdown"
    )

@restricted
async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    creds = load_creds()
    if not creds:
        await update.message.reply_text("No hay credenciales guardadas. Revisá el archivo credentials.json")
        return

    if "task" in context.user_data and not context.user_data["task"].done():
        await update.message.reply_text("Ya hay un despliegue en curso. Usá /cancel para detenerlo.")
        return

    await update.message.reply_text("⏳ Iniciando despliegue de VM Ampere A1...")

    session_id = create_session()
    context.user_data["session_id"] = session_id

    pem_content = base64.b64decode(creds["pem_b64"]).decode("ascii")

    write_tfvars(
        session_id,
        creds["tenancy_ocid"],
        creds["user_ocid"],
        creds["fingerprint"],
        pem_content,
        "ubuntu2404",
    )

    task = context.application.create_task(
        _deploy_loop(update.effective_chat.id, session_id, context, update.message.message_id)
    )
    context.user_data["task"] = task

@restricted
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = context.user_data.get("task")
    if task and not task.done():
        task.cancel()
        await update.message.reply_text("Despliegue cancelado.")
    else:
        await update.message.reply_text("No hay ningún despliegue en curso.")

    session_id = context.user_data.get("session_id")
    if session_id:
        cleanup_session(session_id)
        context.user_data.pop("session_id", None)

async def _deploy_loop(chat_id: int, session_id: str, context: ContextTypes.DEFAULT_TYPE, msg_id: int = None):
    attempt = 0
    status_msg = None
    try:
        while True:
            attempt += 1
            success, result = await run_terraform(session_id)

            if success:
                ips_public = ", ".join(result["public_ips"]) if result["public_ips"] else "N/A"
                ips_private = ", ".join(result["private_ips"]) if result["private_ips"] else "N/A"

                msg = (
                    "✅ *VM creada exitosamente!*\n\n"
                    f"• *IP Pública:* `{ips_public}`\n"
                    f"• *IP Privada:* `{ips_private}`\n"
                    f"• *SO:* Ubuntu 24.04\n\n"
                    f"*Comando SSH:*\n"
                    f"```\nssh -i oci-id_rsa ubuntu@{ips_public}\n```"
                )
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

                ssh_key = result.get("ssh_private_key", "")
                if ssh_key:
                    import tempfile, os as os_mod
                    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
                    tmp.write(ssh_key)
                    tmp.close()
                    with open(tmp.name, "rb") as f:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            filename="oci-id_rsa.pem",
                            caption="🔑 Clave SSH privada"
                        )
                    os_mod.unlink(tmp.name)

                await context.bot.send_message(
                    chat_id=chat_id,
                    text="La VM está lista. Podés gestionarla desde el panel de Oracle.",
                )
                cleanup_session(session_id)
                return

            error_text = result.get("error", "")
            error_text = _extract_error(error_text)

            if is_capacity_error(error_text):
                text = f"⚠️ Sin capacidad disponible (intento #{attempt}).\nEsperando 60 segundos para reintentar..."
                if status_msg:
                    try:
                        await status_msg.edit_text(text)
                    except:
                        status_msg = await context.bot.send_message(chat_id=chat_id, text=text)
                else:
                    status_msg = await context.bot.send_message(chat_id=chat_id, text=text)
                await asyncio.sleep(60)
            else:
                safe_error = error_text[:1500].encode("ascii", errors="replace").decode("ascii")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Error al crear la VM (intento #{attempt}):\n`{safe_error}`",
                    parse_mode="Markdown"
                )
                return
    except asyncio.CancelledError:
        await context.bot.send_message(chat_id=chat_id, text="Despliegue cancelado por el usuario.")
    finally:
        context.user_data.pop("task", None)
        context.user_data.pop("session_id", None)

def _extract_error(text: str) -> str:
    lines = text.split("\n")
    error_lines = []
    capture = False
    for line in reversed(lines):
        if "Error:" in line or "╷" in line:
            error_lines.insert(0, line)
            capture = True
        elif capture:
            if line.strip() == "" or "╵" in line:
                break
            error_lines.insert(0, line)
    result = "\n".join(error_lines) if error_lines else text
    return result[:1500]

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("create", cmd_create))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    print("Bot iniciado. Presioná Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
