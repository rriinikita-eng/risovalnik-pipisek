from html.parser import HTMLParser

from starlette.testclient import TestClient

from main import app


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.scripts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        if tag == "link" and a.get("href"):
            self.links.append(a["href"])


def page():
    client = TestClient(app)
    r = client.get("/app/")
    assert r.status_code == 200
    p = Page()
    p.feed(r.text)
    return p


def test_canvas_present():
    p = page()
    assert any(tag == "canvas" for tag, _ in p.tags)


def test_toolbar_controls():
    p = page()
    inputs = [a for tag, a in p.tags if tag == "input"]
    assert any(a.get("type") == "color" for a in inputs)
    assert any(a.get("type") == "range" for a in inputs)
    buttons = [a for tag, a in p.tags if tag == "button"]
    assert len(buttons) >= 6


def test_scripts_and_style_linked():
    p = page()
    assert any("logic.js" in s for s in p.scripts)
    assert any("app.js" in s for s in p.scripts)
    assert any("style.css" in h for h in p.links)


def test_static_files_served():
    client = TestClient(app)
    for path in ["/app/style.css", "/app/logic.js", "/app/app.js"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.text.strip() != "", path
