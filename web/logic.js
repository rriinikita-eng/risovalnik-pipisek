const UNDO_LIMIT = 20;

function createHistory() {
  const history = {
    strokes: [],
    redoStack: [],
    undone: 0,
    push(stroke) {
      this.strokes.push(stroke);
      this.redoStack = [];
      this.undone = 0;
    },
    undo() {
      if (this.strokes.length === 0 || this.undone >= UNDO_LIMIT) return false;
      this.redoStack.push(this.strokes.pop());
      this.undone += 1;
      return true;
    },
    redo() {
      if (this.redoStack.length === 0) return false;
      this.strokes.push(this.redoStack.pop());
      if (this.undone > 0) this.undone -= 1;
      return true;
    },
    canUndo() {
      return this.strokes.length > 0 && this.undone < UNDO_LIMIT;
    },
    canRedo() {
      return this.redoStack.length > 0;
    },
    clear() {
      this.strokes = [];
      this.redoStack = [];
      this.undone = 0;
    }
  };
  return history;
}

function scalePoint(x, y, rect, canvasWidth, canvasHeight) {
  const sx = rect.width ? canvasWidth / rect.width : 1;
  const sy = rect.height ? canvasHeight / rect.height : 1;
  return {
    x: (x - rect.left) * sx,
    y: (y - rect.top) * sy
  };
}

function clampThickness(value) {
  const n = Number(value);
  if (!isFinite(n)) return 1;
  return Math.min(50, Math.max(1, n));
}

function eraserColor(background) {
  return background === "white" ? "#ffffff" : "rgba(0,0,0,0)";
}

function exportOptions(background) {
  return {
    type: "image/png",
    fillBackground: background === "white"
  };
}

if (typeof module !== "undefined") {
  module.exports = {
    UNDO_LIMIT,
    createHistory,
    scalePoint,
    clampThickness,
    eraserColor,
    exportOptions
  };
}
