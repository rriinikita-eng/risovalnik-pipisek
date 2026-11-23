import os
import tempfile

_tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(_tmp, "web"), exist_ok=True)
with open(os.path.join(_tmp, "web", "index.html"), "w", encoding="utf-8") as f:
    f.write("<!doctype html><html><body><canvas id='board'></canvas></body></html>")
os.chdir(_tmp)

os.environ["BOT_TOKEN"] = "123456:TEST"
os.environ["WEBHOOK_SECRET"] = "test-secret"
os.environ["RENDER_EXTERNAL_URL"] = "https://example.onrender.com"
os.environ["PORT"] = "8000"

from starlette.testclient import TestClient

import main

client = TestClient(main.app)

UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 0,
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "is_bot": False, "first_name": "T"},
        "text": "hello",
    },
}


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200


def test_webhook_without_secret_rejected():
    r = client.post("/webhook", json=UPDATE)
    assert r.status_code in (401, 403)


def test_webhook_with_wrong_secret_rejected():
    r = client.post(
        "/webhook",
        json=UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert r.status_code in (401, 403)


def test_webhook_with_correct_secret_accepted():
    r = client.post(
        "/webhook",
        json=UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )
    assert r.status_code == 200


def test_app_page_served():
    r = client.get("/app/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
