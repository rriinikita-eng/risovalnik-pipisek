import json
import pathlib
import subprocess

LOGIC = pathlib.Path(__file__).resolve().parents[1] / "web" / "logic.js"


def run(code):
    script = (
        "const L = require(" + json.dumps(str(LOGIC)) + ");"
        "const out = (() => {" + code + "})();"
        "process.stdout.write(JSON.stringify(out));"
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_undo_stops_at_20_steps():
    out = run(
        "const h = L.createHistory();"
        "for (let i = 0; i < 25; i++) h.push({id: i});"
        "let undone = 0;"
        "while (h.undo()) undone++;"
        "return {undone: undone, left: h.strokes.length};"
    )
    assert out["undone"] == 20
    assert out["left"] == 5


def test_redo_restores_undone_strokes():
    out = run(
        "const h = L.createHistory();"
        "h.push({id: 1}); h.push({id: 2}); h.push({id: 3});"
        "h.undo(); h.undo();"
        "const afterUndo = h.strokes.map(s => s.id);"
        "h.redo();"
        "return {afterUndo: afterUndo, afterRedo: h.strokes.map(s => s.id)};"
    )
    assert out["afterUndo"] == [1]
    assert out["afterRedo"] == [1, 2]


def test_new_stroke_clears_redo():
    out = run(
        "const h = L.createHistory();"
        "h.push({id: 1}); h.push({id: 2});"
        "h.undo();"
        "h.push({id: 3});"
        "return {canRedo: h.canRedo(), ids: h.strokes.map(s => s.id)};"
    )
    assert out["canRedo"] is False
    assert out["ids"] == [1, 3]


def test_coordinates_scale_to_canvas():
    out = run(
        "const rect = {left: 10, top: 20, width: 200, height: 100};"
        "return L.scalePoint(110, 70, rect, 400, 200);"
    )
    assert out["x"] == 200
    assert out["y"] == 100


def test_thickness_clamped_to_1_50():
    out = run("return [L.clampThickness(0), L.clampThickness(100), L.clampThickness(12)];")
    assert out == [1, 50, 12]


def test_eraser_color_matches_background():
    out = run("return [L.eraserColor('white'), L.eraserColor('transparent')];")
    assert out[0] == "#ffffff"
    assert out[1] == "rgba(0,0,0,0)"


def test_png_export_settings_follow_background():
    out = run("return [L.exportOptions('white'), L.exportOptions('transparent')];")
    assert out[0]["type"] == "image/png"
    assert out[0]["fillBackground"] is True
    assert out[1]["type"] == "image/png"
    assert out[1]["fillBackground"] is False
