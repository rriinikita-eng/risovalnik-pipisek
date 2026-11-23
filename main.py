import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

API_BASE = "https://api.telegram.org"


def api_url(method: str) -> str:
    return f"{API_BASE}/bot{BOT_TOKEN}/{method}"


def start_reply_markup() -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Открыть рисовалку",
                    "web_app": {"url": RENDER_EXTERNAL_URL + "/app/"},
                }
            ]
        ]
    }


async def send_start_message(chat_id: int) -> None:
    payload = {
        "chat_id": chat_id,
        "text": "Открыть рисовалку",
        "reply_markup": start_reply_markup(),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(api_url("sendMessage"), json=payload)
        response.raise_for_status()


async def handle_update(update: dict) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    text = message.get("text") or ""
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    if text.strip() == "/start" or text.strip().startswith("/start "):
        await send_start_message(chat_id)


async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def webhook(request: Request) -> PlainTextResponse:
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not WEBHOOK_SECRET or token != WEBHOOK_SECRET:
        return PlainTextResponse("forbidden", status_code=403)
    try:
        update = await request.json()
    except ValueError:
        return PlainTextResponse("bad request", status_code=400)
    if isinstance(update, dict):
        await handle_update(update)
    return PlainTextResponse("ok")


async def on_startup() -> None:
    if not BOT_TOKEN or not RENDER_EXTERNAL_URL:
        return
    payload = {
        "url": RENDER_EXTERNAL_URL + "/webhook",
        "secret_token": WEBHOOK_SECRET,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(api_url("setWebhook"), json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        print("setWebhook failed:", exc)


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/webhook", webhook, methods=["POST"]),
    Mount("/app", app=StaticFiles(directory="web", html=True, check_dir=False)),
]

@asynccontextmanager
async def lifespan(app: Starlette):
    await on_startup()
    yield


app = Starlette(routes=routes, lifespan=lifespan)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
