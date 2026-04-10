import os
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from agent import run_agent
from database import get_db
from models import Chat, HandoffEvent, Intent, Message, Session, User, WorkflowState

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_USERS = set(
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=3),
    ],
)
logger = logging.getLogger(__name__)

# In-memory session cache: user_id -> session_id
_active_sessions: dict[int, int] = {}
# In-memory message history: user_id -> [{"role": ..., "content": ...}]
_histories: dict[int, list[dict]] = {}


def _upsert_user(db, tg_user) -> User:
    user = db.get(User, tg_user.id)
    if not user:
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            is_allowed=tg_user.id in ALLOWED_USERS,
        )
        db.add(user)
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
        user.is_allowed = tg_user.id in ALLOWED_USERS
    return user


def _upsert_chat(db, tg_chat) -> Chat:
    chat = db.get(Chat, tg_chat.id)
    if not chat:
        chat = Chat(id=tg_chat.id, type=tg_chat.type, title=getattr(tg_chat, "title", None))
        db.add(chat)
    return chat


def _get_or_create_session(db, user_id: int) -> Session:
    session_id = _active_sessions.get(user_id)
    if session_id:
        session = db.get(Session, session_id)
        if session and session.is_active:
            return session
    session = Session(user_id=user_id)
    db.add(session)
    db.flush()  # get session.id
    _active_sessions[user_id] = session.id
    db.add(WorkflowState(session_id=session.id, state="active"))
    return session


def is_allowed(update: Update) -> bool:
    return update.effective_user.id in ALLOWED_USERS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    tg_chat = update.effective_chat

    with get_db() as db:
        _upsert_user(db, tg_user)
        _upsert_chat(db, tg_chat)

    if not is_allowed(update):
        logger.warning("Unauthorized /start from user %s", tg_user.id)
        await update.message.reply_text("Access denied.")
        return

    # Close any existing session and open a new one
    with get_db() as db:
        old_id = _active_sessions.pop(tg_user.id, None)
        if old_id:
            old = db.get(Session, old_id)
            if old:
                old.is_active = False
                db.add(WorkflowState(session_id=old_id, state="closed"))
        _histories[tg_user.id] = []
        _get_or_create_session(db, tg_user.id)

    logger.info("User %s started a new session", tg_user.id)
    await update.message.reply_text("Hello! I'm your AI assistant. Ask me anything.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    tg_chat = update.effective_chat
    text = update.message.text

    if not is_allowed(update):
        logger.warning("Unauthorized message from user %s", tg_user.id)
        await update.message.reply_text("Access denied.")
        return

    with get_db() as db:
        _upsert_user(db, tg_user)
        _upsert_chat(db, tg_chat)
        session = _get_or_create_session(db, tg_user.id)

        # Save user message
        user_msg = Message(
            session_id=session.id,
            user_id=tg_user.id,
            chat_id=tg_chat.id,
            telegram_message_id=update.message.message_id,
            role="user",
            content=text,
        )
        db.add(user_msg)
        db.flush()

    # Build history and run agent
    history = _histories.setdefault(tg_user.id, [])
    history.append({"role": "user", "content": text})

    logger.info("Message from %s: %s", tg_user.id, text)
    response, intent_name = await run_agent(history)
    history.append({"role": "assistant", "content": response})
    logger.info("Response to %s: %s", tg_user.id, response)

    with get_db() as db:
        session = _get_or_create_session(db, tg_user.id)

        # Save intent on the user message
        if intent_name:
            db.add(Intent(message_id=user_msg.id, name=intent_name, confidence=1.0))

        # Save assistant reply
        db.add(Message(
            session_id=session.id,
            user_id=tg_user.id,
            chat_id=tg_chat.id,
            role="assistant",
            content=response,
        ))

        # Record handoff if intent is support_request or complaint
        if intent_name in ("support_request", "complaint"):
            db.add(HandoffEvent(
                session_id=session.id,
                from_agent="Assistant",
                to_agent="HumanSupport",
                reason=intent_name,
                extra_data={"trigger_message_id": user_msg.id},
            ))
            db.add(WorkflowState(
                session_id=session.id,
                state="pending_handoff",
                data={"reason": intent_name},
            ))
            logger.info("Handoff event created for user %s, reason: %s", tg_user.id, intent_name)

    await update.message.reply_text(response)


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()

