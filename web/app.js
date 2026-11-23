(function () {
  "use strict";

  var api = (typeof window !== "undefined" && (window.DrawingLogic || window.Logic)) || {};

  function use(name, fallback) {
    if (typeof api[name] === "function") return api[name];
    if (typeof window !== "undefined" && typeof window[name] === "function") return window[name];
    return fallback;
  }

  var createHistory = use("createHistory", function (limit) {
    var items = [];
    var pos = -1;
    return {
      push: function (snap) {
        items = items.slice(0, pos + 1);
        items.push(snap);
        if (items.length > limit) items.shift();
        pos = items.length - 1;
      },
      undo: function () {
        if (pos > 0) pos -= 1;
        return items[pos] || null;
      },
      redo: function () {
        if (pos < items.length - 1) pos += 1;
        return items[pos] || null;
      },
      canUndo: function () { return pos > 0; },
      canRedo: function () { return pos < items.length - 1; }
    };
  });

  var toCanvasPoint = use("toCanvasPoint", function (x, y, rect, width, height) {
    return {
      x: (x - rect.left) * (width / rect.width),
      y: (y - rect.top) * (height / rect.height)
    };
  });

  var clampThickness = use("clampThickness", function (value, min, max) {
    var n = Number(value);
    if (!isFinite(n)) n = min;
    return Math.min(max, Math.max(min, n));
  });

  var eraserColor = use("eraserColor", function (bg) {
    return bg || "#ffffff";
  });

  var canvas = document.getElementById("board");
  var ctx = canvas.getContext("2d");
  var colorInput = document.getElementById("color");
  var sizeInput = document.getElementById("size");
  var brushBtn = document.getElementById("brush");
  var eraserBtn = document.getElementById("eraser");
  var undoBtn = document.getElementById("undo");
  var redoBtn = document.getElementById("redo");
  var clearBtn = document.getElementById("clear");
  var bgBtn = document.getElementById("bg");
  var saveBtn = document.getElementById("save");

  var history = createHistory(40);
  var color = colorInput.value || "#000000";
  var thickness = clampThickness(sizeInput.value, 1, 60);
  var erasing = false;
  var background = "#ffffff";
  var drawing = false;
  var last = null;

  function scale() {
    var rect = canvas.getBoundingClientRect();
    return rect.width ? canvas.width / rect.width : 1;
  }

  function paintBackground() {
    ctx.save();
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.restore();
  }

  function resize() {
    var rect = canvas.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(1, Math.round(rect.width * dpr));
    var h = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width === w && canvas.height === h) return;
    var snapshot = canvas.width && canvas.height ? canvas.toDataURL("image/png") : null;
    canvas.width = w;
    canvas.height = h;
    paintBackground();
    if (snapshot) {
      var img = new Image();
      img.onload = function () { ctx.drawImage(img, 0, 0, w, h); };
      img.src = snapshot;
    }
  }

  function point(e) {
    return toCanvasPoint(e.clientX, e.clientY, canvas.getBoundingClientRect(), canvas.width, canvas.height);
  }

  function strokeColor() {
    return erasing ? eraserColor(background) : color;
  }

  function dot(p) {
    ctx.beginPath();
    ctx.fillStyle = strokeColor();
    ctx.arc(p.x, p.y, (thickness * scale()) / 2, 0, Math.PI * 2);
    ctx.fill();
  }

  function down(e) {
    e.preventDefault();
    if (canvas.setPointerCapture) canvas.setPointerCapture(e.pointerId);
    history.push(ctx.getImageData(0, 0, canvas.width, canvas.height));
    drawing = true;
    last = point(e);
    dot(last);
  }

  function move(e) {
    if (!drawing) return;
    e.preventDefault();
    var p = point(e);
    ctx.strokeStyle = strokeColor();
    ctx.lineWidth = thickness * scale();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(last.x, last.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    last = p;
  }

  function up() {
    drawing = false;
    last = null;
  }

  function restore(snap) {
    if (snap) ctx.putImageData(snap, 0, 0);
  }

  function setEraser(on) {
    erasing = on;
    brushBtn.classList.toggle("active", !on);
    eraserBtn.classList.toggle("active", on);
  }

  brushBtn.addEventListener("click", function () { setEraser(false); });
  eraserBtn.addEventListener("click", function () { setEraser(true); });
  colorInput.addEventListener("input", function () {
    color = colorInput.value;
    setEraser(false);
  });
  sizeInput.addEventListener("input", function () {
    thickness = clampThickness(sizeInput.value, 1, 60);
  });
  undoBtn.addEventListener("click", function () { restore(history.undo()); });
  redoBtn.addEventListener("click", function () { restore(history.redo()); });
  clearBtn.addEventListener("click", function () {
    if (!window.confirm("Очистить холст?")) return;
    history.push(ctx.getImageData(0, 0, canvas.width, canvas.height));
    paintBackground();
  });
  bgBtn.addEventListener("click", function () {
    background = background === "#ffffff" ? "#111111" : "#ffffff";
    paintBackground();
  });
  saveBtn.addEventListener("click", function () {
    var link = document.createElement("a");
    link.download = "drawing.png";
    link.href = canvas.toDataURL("image/png");
    link.click();
  });

  Array.prototype.forEach.call(document.querySelectorAll(".swatch"), function (btn) {
    btn.addEventListener("click", function () {
      color = btn.getAttribute("data-color");
      colorInput.value = color;
      setEraser(false);
    });
  });

  canvas.addEventListener("pointerdown", down);
  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerup", up);
  canvas.addEventListener("pointercancel", up);
  canvas.addEventListener("pointerleave", up);
  window.addEventListener("resize", resize);

  resize();
  history.push(ctx.getImageData(0, 0, canvas.width, canvas.height));
})();
