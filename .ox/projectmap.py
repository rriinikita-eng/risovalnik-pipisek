"""Карта проекта (этап 0.4, step16): индекс файлов, классов и функций и граф импортов.

Модуль только на стандартной библиотеке и ничего не импортирует из app.*: тяжёлую часть
(обход репозитория и разбор через ast) выполняет GitHub Actions как скрипт

    python app/projectmap.py --root DIR --sha SHA40 --out project-map.json

а бот использует те же функции, чтобы проверить пришедшую карту (validate_map, read_map_zip).
Код проекта при разборе не исполняется: ast.parse только читает текст.

Что попадает в карту. Для Python-файлов: классы, функции и методы с сигнатурами, первая строка
докстринга, импорты, имена констант. Для остальных текстовых файлов: путь, размер, первые строки
или заголовки Markdown. Тел функций, комментариев и значений нет. Граф: какой файл импортирует
какие файлы проекта.

Защита. Символические ссылки не читаются. Файлы с секретными именами (.env, ключи, credentials)
не читаются совсем. В сохранённые строки не пропускаем токены и пароли: похожее на секрет
заменяется на [скрыто]. Карта детерминирована: один и тот же коммит даёт тот же JSON.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import stat
import sys
import warnings
import zipfile
from typing import Any, Dict, List, Optional, Set, Tuple

MAP_VERSION = 1
ARTIFACT_JSON_NAME = "project-map.json"

MAX_FILE_BYTES = 200_000  # файлы больше в карту не берём (как в снапшотах этапа 0.3)
MAX_FILES_SCANNED = 5000  # сколько файлов вообще просматриваем
MAX_MAP_BYTES = 1_500_000  # размер JSON карты; не влезло: truncated=true
MAX_SKIPPED_LISTED = 100
MAX_ITEMS_PER_FILE = 200  # верхнеуровневых классов и функций на файл
MAX_METHODS_PER_CLASS = 60
MAX_IMPORTS_PER_FILE = 60
MAX_CONSTANTS_PER_FILE = 30
HEAD_LINES = 12
HEAD_LINE_CHARS = 160
HEADINGS_LIMIT = 20
HEAD_MAX_FILE_BYTES = 100_000  # у больших файлов первые строки не берём
SIG_CHARS = 200
DOC_CHARS = 120

SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".idea", ".vscode", "dist", "build", "site-packages", ".eggs",
})
_SKIP_EXT = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf", ".zip", ".gz", ".tar",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".webm", ".db", ".sqlite", ".sqlite3",
    ".jar", ".exe", ".pyc", ".pyo", ".so", ".dll", ".dylib", ".bin", ".class",
})
_LOCK_NAMES = frozenset({"package-lock.json", "yarn.lock", "poetry.lock", "pnpm-lock.yaml", "pipfile.lock"})
_ENV_EXAMPLES = frozenset({".env.example", ".env.sample", ".env.template"})
# Имена, по которым нечитаемый файл считаем секретным. Для .py не применяется: в карту
# из Python-файла попадают только имена и сигнатуры, а не тексты.
_SECRET_NAME_RE = re.compile(
    r"(^\.env($|\.))|(\.(pem|key|p12|pfx|jks|keystore)$)|secret|credential|(^id_(rsa|dsa|ecdsa|ed25519))"
    r"|(^\.(npmrc|pypirc|netrc)$)",
    re.IGNORECASE,
)

_CTRL_RE = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
_SECRET_KEYS = r"(?:token|secret|passw(?:or)?d|api[_-]?key|private[_-]?key)"
_SECRET_ASSIGN_RE = re.compile(
    rf"([\w.-]*{_SECRET_KEYS}[\w.-]*)(\s*[=:]\s*)(\S{{6,}})", re.IGNORECASE
)
_SECRET_DEFAULT_RE = re.compile(
    rf"(\b[\w.-]*{_SECRET_KEYS}[\w.-]*\b[^=\n]{{0,60}}=\s*)([\"'])[^\"']{{4,}}\2", re.IGNORECASE
)
_SECRET_SHAPES_RE = re.compile(
    r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"
    r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    r"|\bsk-[A-Za-z0-9_-]{16,}\b"
    r"|\bgsk_[A-Za-z0-9]{16,}\b"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
)
_URL_CREDS_RE = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CONST_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class MapFormatError(ValueError):
    """Карта или архив с ней не прошли проверку. Текст безопасно показывать в логе."""


# --- очистка строк ---

def _clean(text: Any, limit: int = HEAD_LINE_CHARS) -> str:
    """Одна короткая строка без управляющих символов; похожее на секрет заменено на [скрыто]."""
    text = _CTRL_RE.sub(" ", str(text))
    text = " ".join(text.split())
    text = _URL_CREDS_RE.sub(r"\1[скрыто]@", text)
    text = _SECRET_SHAPES_RE.sub("[скрыто]", text)
    text = _SECRET_DEFAULT_RE.sub(r"\1\2[скрыто]\2", text)
    text = _SECRET_ASSIGN_RE.sub(r"\1\2[скрыто]", text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _first_line(doc: Optional[str]) -> str:
    if not doc:
        return ""
    for line in doc.splitlines():
        if line.strip():
            return _clean(line, DOC_CHARS)
    return ""


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


# --- разбор Python ---

def _signature(node: Any) -> str:
    try:
        args = ast.unparse(node.args)
        returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    except Exception:  # экзотический синтаксис: сигнатуру не восстановили, но функцию не теряем
        return "(...)"
    return _clean(f"({args}){returns}", SIG_CHARS)


def _function_entry(node: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"name": node.name, "line": node.lineno, "sig": _signature(node)}
    if isinstance(node, ast.AsyncFunctionDef):
        entry["async"] = True
    doc = _first_line(ast.get_docstring(node))
    if doc:
        entry["doc"] = doc
    decorators = []
    for decorator in node.decorator_list[:3]:
        try:
            decorators.append(_clean(ast.unparse(decorator), 60))
        except Exception:
            continue
    if decorators:
        entry["decorators"] = decorators
    return entry


def _class_entry(node: ast.ClassDef) -> Dict[str, Any]:
    bases = []
    for base in node.bases[:5]:
        try:
            bases.append(_clean(ast.unparse(base), 60))
        except Exception:
            continue
    entry: Dict[str, Any] = {"name": node.name, "line": node.lineno}
    if bases:
        entry["bases"] = bases
    doc = _first_line(ast.get_docstring(node))
    if doc:
        entry["doc"] = doc
    methods = [
        _function_entry(item) for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if len(methods) > MAX_METHODS_PER_CLASS:
        entry["omitted_methods"] = len(methods) - MAX_METHODS_PER_CLASS
        methods = methods[:MAX_METHODS_PER_CLASS]
    entry["methods"] = methods
    return entry


def _raw_imports(tree: ast.AST) -> List[Tuple[int, str, Tuple[str, ...]]]:
    """Все import в файле, и верхнего уровня, и внутри функций: (уровень точек, модуль, имена)."""
    found: List[Tuple[int, str, Tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((0, alias.name, ()))
        elif isinstance(node, ast.ImportFrom):
            found.append((node.level or 0, node.module or "", tuple(a.name for a in node.names)))
    return found


def _import_labels(raw: List[Tuple[int, str, Tuple[str, ...]]]) -> List[str]:
    labels: Set[str] = set()
    for level, module, names in raw:
        if module:
            labels.add("." * level + module)
        elif level:  # from . import x, from .. import y
            for name in names:
                labels.add("." * level + name)
    return sorted(_clean(label, 100) for label in labels)[:MAX_IMPORTS_PER_FILE]


def _analyze_python(source: str) -> Tuple[Dict[str, Any], List[Tuple[int, str, Tuple[str, ...]]]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # SyntaxWarning про «\d» и подобное нам не нужен
        tree = ast.parse(source)
    fields: Dict[str, Any] = {}
    doc = _first_line(ast.get_docstring(tree))
    if doc:
        fields["doc"] = doc
    classes: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []
    constants: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes.append(_class_entry(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_function_entry(node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and _CONST_RE.match(target.id) and target.id not in constants:
                    constants.append(target.id)
    omitted = max(0, len(classes) + len(functions) - MAX_ITEMS_PER_FILE)
    if omitted:
        classes = classes[:MAX_ITEMS_PER_FILE]
        functions = functions[: max(0, MAX_ITEMS_PER_FILE - len(classes))]
        fields["omitted_items"] = omitted
    raw = _raw_imports(tree)
    imports = _import_labels(raw)
    if imports:
        fields["imports"] = imports
    if constants:
        fields["constants"] = constants[:MAX_CONSTANTS_PER_FILE]
    if classes:
        fields["classes"] = classes
    if functions:
        fields["functions"] = functions
    return fields, raw


# --- остальные текстовые файлы ---

def _head(rel: str, text: str, size: int) -> List[str]:
    if size > HEAD_MAX_FILE_BYTES:
        return []
    lines = text.splitlines()
    if rel.lower().endswith((".md", ".markdown")):
        headings = [_clean(line, 100) for line in lines if _HEADING_RE.match(line)][:HEADINGS_LIMIT]
        if headings:
            return headings
    head: List[str] = []
    for line in lines:
        if not line.strip():
            continue
        head.append(_clean(line))
        if len(head) >= HEAD_LINES:
            break
    return head


# --- обход ---

def _skip_dir(name: str) -> bool:
    if name in SKIP_DIRS or name.endswith(".egg-info"):
        return True
    return name.startswith(".") and name != ".github"


def _skip_reason(name: str, size: int) -> Optional[str]:
    low = name.lower()
    is_python = low.endswith(".py")
    if low in _LOCK_NAMES:
        return "служебный"
    if os.path.splitext(low)[1] in _SKIP_EXT:
        return "бинарный"
    if not is_python and low not in _ENV_EXAMPLES and _SECRET_NAME_RE.search(low):
        return "секреты"
    if size > MAX_FILE_BYTES:
        return "слишком большой"
    return None


def _read_text(full: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        with open(full, "rb") as handle:
            blob = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None, "не прочитан"
    if len(blob) > MAX_FILE_BYTES:
        return None, "слишком большой"
    if b"\x00" in blob:
        return None, "бинарный"
    try:
        return blob.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "не UTF-8"


def _is_test(rel: str) -> bool:
    parts = rel.split("/")
    base = parts[-1]
    return (
        any(part in ("tests", "test") for part in parts[:-1])
        or base.startswith("test_") or base.endswith("_test.py") or base == "conftest.py"
    )


def _python_entry(rel: str, size: int, text: str) -> Tuple[Dict[str, Any], List[Tuple[int, str, Tuple[str, ...]]]]:
    entry: Dict[str, Any] = {"path": rel, "kind": "python", "size": size, "lines": _count_lines(text)}
    if _is_test(rel):
        entry["test"] = True
    try:
        fields, raw = _analyze_python(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError, OverflowError) as exc:
        # Текст ошибки не берём: в нём может быть кусок исходника. Только тип и строка.
        line = getattr(exc, "lineno", None)
        entry["parse_error"] = type(exc).__name__ + (f", строка {line}" if isinstance(line, int) else "")
        return entry, []
    entry.update(fields)
    return entry, raw


def _text_entry(rel: str, size: int, text: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"path": rel, "kind": "text", "size": size, "lines": _count_lines(text)}
    ext = os.path.splitext(rel)[1].lower().lstrip(".")
    if ext:
        entry["lang"] = ext
    head = _head(rel, text, size)
    if head:
        entry["head"] = head
    return entry


# --- граф импортов ---

def _module_index(py_paths: List[str]) -> Dict[str, str]:
    """Точечное имя модуля -> путь файла. Раскладка src/ тоже понимается."""
    index: Dict[str, str] = {}
    for path in sorted(py_paths):
        stem = path[:-3]
        parts = stem.split("/")
        variants = [parts]
        if parts[0] == "src" and len(parts) > 1:
            variants.append(parts[1:])
        for variant in variants:
            if variant[-1] == "__init__":
                variant = variant[:-1]
            if variant:
                index.setdefault(".".join(variant), path)
    return index


def _resolve(path: str, raw: List[Tuple[int, str, Tuple[str, ...]]], index: Dict[str, str]) -> List[str]:
    package = path.split("/")[:-1]
    targets: Set[str] = set()
    for level, module, names in raw:
        if level:
            keep = len(package) - (level - 1)
            if keep < 0:
                continue
            base = package[:keep] + (module.split(".") if module else [])
        else:
            base = module.split(".") if module else []
        if not base and not names:
            continue
        if not names:
            # import a.b.c: берём самый длинный существующий префикс
            for i in range(len(base), 0, -1):
                hit = index.get(".".join(base[:i]))
                if hit:
                    targets.add(hit)
                    break
            continue
        dotted = ".".join(base)
        attribute_import = False
        for name in names:
            sub = index.get(f"{dotted}.{name}" if dotted else name)
            if sub:
                targets.add(sub)
            else:
                attribute_import = True
        if attribute_import and dotted:
            for i in range(len(base), 0, -1):
                hit = index.get(".".join(base[:i]))
                if hit:
                    targets.add(hit)
                    break
    targets.discard(path)
    return sorted(targets)


# --- сборка ---

def build_map(root: Any, commit_sha: str = "") -> Dict[str, Any]:
    """Строит карту проекта из папки root. Файлы только читаются, код не запускается."""
    root = os.path.abspath(os.fspath(root))
    if not os.path.isdir(root):
        raise ValueError("корневая папка не найдена")

    candidates: List[Tuple[str, str, int]] = []  # (относительный путь, полный путь, размер)
    skipped: List[Dict[str, str]] = []
    skipped_total = 0
    scanned = 0
    truncated = False

    def skip(rel: str, reason: str) -> None:
        nonlocal skipped_total
        skipped_total += 1
        if len(skipped) < MAX_SKIPPED_LISTED:
            skipped.append({"path": rel, "reason": reason})

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            scanned += 1
            if scanned > MAX_FILES_SCANNED:
                truncated = True
                break
            rel = name if rel_dir == "." else f"{rel_dir.replace(os.sep, '/')}/{name}"
            try:
                rel.encode("utf-8")
            except UnicodeEncodeError:
                skip(rel.encode("utf-8", "replace").decode("utf-8"), "имя не UTF-8")
                continue
            full = os.path.join(dirpath, name)
            try:
                info = os.lstat(full)
            except OSError:
                skip(rel, "не прочитан")
                continue
            if stat.S_ISLNK(info.st_mode):
                skip(rel, "ссылка")
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            reason = _skip_reason(name, info.st_size)
            if reason:
                skip(rel, reason)
                continue
            candidates.append((rel, full, info.st_size))
        if truncated:
            break

    # Сначала Python (он нужен для графа), потом остальные: при нехватке места режем хвост «остальных».
    candidates.sort(key=lambda item: (not item[0].endswith(".py"), item[0]))
    entries: Dict[str, Dict[str, Any]] = {}
    raw_by_path: Dict[str, List[Tuple[int, str, Tuple[str, ...]]]] = {}
    used = 0
    omitted = 0
    for rel, full, size in candidates:
        text, reason = _read_text(full)
        if text is None:
            skip(rel, reason or "не прочитан")
            continue
        if rel.endswith(".py"):
            entry, raw = _python_entry(rel, size, text)
        else:
            entry, raw = _text_entry(rel, size, text), []
        cost = len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
        if used + cost > MAX_MAP_BYTES - 100_000:  # запас под граф и служебные поля
            truncated = True
            omitted += 1
            continue
        used += cost
        entries[rel] = entry
        if raw:
            raw_by_path[rel] = raw

    index = _module_index([p for p, e in entries.items() if e["kind"] == "python"])
    imports: Dict[str, List[str]] = {}
    for path in sorted(raw_by_path):
        targets = _resolve(path, raw_by_path[path], index)
        if targets:
            imports[path] = targets

    files = [entries[p] for p in sorted(entries)]
    python_files = [f for f in files if f["kind"] == "python"]
    return {
        "version": MAP_VERSION,
        "commit_sha": commit_sha,
        "stats": {
            "files": len(files),
            "python_files": len(python_files),
            "test_files": sum(1 for f in python_files if f.get("test")),
            "classes": sum(len(f.get("classes", ())) for f in python_files),
            "functions": sum(
                len(f.get("functions", ())) + sum(len(c["methods"]) for c in f.get("classes", ()))
                for f in python_files
            ),
            "lines": sum(f["lines"] for f in files),
            "bytes": sum(f["size"] for f in files),
        },
        "files": files,
        "graph": {"imports": imports},
        "skipped": skipped,
        "skipped_total": skipped_total,
        "truncated": truncated,
        "omitted_files": omitted,
    }


# --- проверка карты, пришедшей извне ---

def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_path(path: Any) -> str:
    if (
        not isinstance(path, str) or not path or len(path) > 500 or "\x00" in path or "\\" in path
        or path.startswith("/") or any(part in ("", "..") for part in path.split("/"))
    ):
        raise MapFormatError("в карте некорректный путь файла")
    return path


def validate_map(data: Any, expected_sha: Optional[str] = None) -> Dict[str, Any]:
    """Проверяет форму карты. Карту делает Actions, но проверяем её как недоверенные данные."""
    if not isinstance(data, dict):
        raise MapFormatError("карта не объект")
    if data.get("version") != MAP_VERSION:
        raise MapFormatError("неизвестная версия карты")
    sha = data.get("commit_sha")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise MapFormatError("в карте нет корректного commit_sha")
    if expected_sha is not None and sha != expected_sha:
        raise MapFormatError("карта построена для другого коммита")
    files = data.get("files")
    if not isinstance(files, list) or len(files) > MAX_FILES_SCANNED:
        raise MapFormatError("в карте некорректный список файлов")
    seen: Set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise MapFormatError("запись файла не объект")
        path = _check_path(entry.get("path"))
        if path in seen:
            raise MapFormatError("в карте повторяется файл")
        seen.add(path)
        if entry.get("kind") not in ("python", "text"):
            raise MapFormatError("у файла неизвестный тип")
        if not _is_int(entry.get("size")) or not _is_int(entry.get("lines")):
            raise MapFormatError("у файла некорректные размер или число строк")
        for key in ("classes", "functions", "head", "imports", "constants"):
            value = entry.get(key)
            if value is not None and not isinstance(value, list):
                raise MapFormatError(f"поле {key} должно быть списком")
        for item in list(entry.get("classes") or []) + list(entry.get("functions") or []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise MapFormatError("у класса или функции нет имени")
        for cls in entry.get("classes") or []:
            methods = cls.get("methods", [])
            if not isinstance(methods, list) or any(
                not isinstance(m, dict) or not isinstance(m.get("name"), str) for m in methods
            ):
                raise MapFormatError("у метода нет имени")
    stats = data.get("stats")
    if not isinstance(stats, dict) or any(not _is_int(v) for v in stats.values()):
        raise MapFormatError("некорректная статистика карты")
    graph = data.get("graph")
    imports = graph.get("imports") if isinstance(graph, dict) else None
    if not isinstance(imports, dict):
        raise MapFormatError("в карте нет графа импортов")
    edges = 0
    for source, targets in imports.items():
        if source not in seen or not isinstance(targets, list):
            raise MapFormatError("в графе импортов неизвестный файл")
        for target in targets:
            if target not in seen:
                raise MapFormatError("в графе импортов неизвестный файл")
        edges += len(targets)
        if edges > 100_000:
            raise MapFormatError("в графе импортов слишком много связей")
    if not isinstance(data.get("truncated"), bool):
        raise MapFormatError("в карте нет признака truncated")
    return data


def read_map_zip(data: bytes, expected_sha: Optional[str] = None) -> Dict[str, Any]:
    """Достаёт и проверяет карту из zip-артефакта Actions. В архиве должен лежать один файл."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        raise MapFormatError("артефакт не zip-архив") from None
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) != 1 or infos[0].filename != ARTIFACT_JSON_NAME:
            raise MapFormatError("в артефакте не тот набор файлов")
        info = infos[0]
        if info.file_size > MAX_MAP_BYTES:
            raise MapFormatError("карта слишком большая")
        try:
            with archive.open(info) as handle:
                blob = handle.read(MAX_MAP_BYTES + 1)
        except (zipfile.BadZipFile, OSError, RuntimeError):
            raise MapFormatError("архив повреждён") from None
    if len(blob) > MAX_MAP_BYTES:
        raise MapFormatError("карта слишком большая")
    try:
        parsed = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise MapFormatError("карта не JSON") from None
    return validate_map(parsed, expected_sha)


def imported_by_counts(map_json: Dict[str, Any]) -> Dict[str, int]:
    """Сколько файлов проекта импортируют каждый файл."""
    counts: Dict[str, int] = {}
    for targets in map_json.get("graph", {}).get("imports", {}).values():
        for target in targets:
            counts[target] = counts.get(target, 0) + 1
    return counts


# --- запуск из Actions ---

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Строит карту проекта (индекс файлов и граф импортов).")
    parser.add_argument("--root", default=".", help="папка проекта")
    parser.add_argument("--sha", required=True, help="полный SHA коммита (40 символов), для которого строится карта")
    parser.add_argument("--out", default=ARTIFACT_JSON_NAME, help="куда записать JSON")
    args = parser.parse_args(argv)
    if not _SHA_RE.match(args.sha):
        print("--sha должен быть полным SHA коммита из 40 символов a-f0-9", file=sys.stderr)
        return 2
    try:
        result = build_map(args.root, args.sha)
        validate_map(result, args.sha)
    except (ValueError, MapFormatError) as exc:
        print(f"Карта не построена: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_MAP_BYTES:
        print("Карта получилась слишком большой", file=sys.stderr)
        return 1
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(payload)
    stats = result["stats"]
    print(
        f"Карта построена: файлов {stats['files']}, Python {stats['python_files']}, "
        f"классов {stats['classes']}, функций {stats['functions']}, "
        f"пропущено {result['skipped_total']}, обрезана: {'да' if result['truncated'] else 'нет'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
