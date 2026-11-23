import os

os.environ["BOT_TOKEN"] = "123456:TEST"
os.environ["WEBHOOK_SECRET"] = "test-secret"
os.environ["RENDER_EXTERNAL_URL"] = "https://example.com"
os.environ["PORT"] = "8000"

from starlette.testclient import TestClient

from main import app, start_reply_markup

client = TestClient(app)
SECRET = "test-secret"
HEADER = "X-Telegram-Bot-Api-Secret-Token"


def test_health_returns_200():
    assert client.get("/health").status_code == 200


def test_webhook_rejects_missing_secret():
    r = client.post("/webhook", json={"update_id": 1})
    assert r.status_code in (401, 403)


def test_webhook_rejects_wrong_secret():
    r = client.post("/webhook", json={"update_id": 1}, headers={HEADER: "wrong"})
    assert r.status_code in (401, 403)


def test_webhook_accepts_right_secret():
    r = client.post("/webhook", json={"update_id": 1}, headers={HEADER: SECRET})
    assert r.status_code == 200


def test_start_button_points_to_app():
    markup = start_reply_markup()
    button = markup["inline_keyboard"][0][0]
    assert button["web_app"]["url"] == "https://example.com/app/"
