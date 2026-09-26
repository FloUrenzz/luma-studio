"""Local HTTP API for Luma Studio. All images remain in this process's memory."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from io import BytesIO
import json
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, quote, urlparse
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

from PIL import Image, UnidentifiedImageError

from processing import EXPORT_FORMATS, FILTERS, ImageDocument


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web"
MAX_UPLOAD = 35 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 100_000_000


class Library:
    """Thread-safe, ordered collection of independent image documents."""

    def __init__(self):
        self.items: dict[str, ImageDocument] = {}
        self.lock = RLock()

    def add(self, data: bytes, name: str) -> str:
        with Image.open(BytesIO(data)) as image:
            image.load()
            if image.width * image.height > 100_000_000:
                raise ValueError("Изображение слишком большое")
            document = ImageDocument(image, Path(name).name or "Изображение")
        key = uuid4().hex
        with self.lock:
            self.items[key] = document
        return key

    def describe(self, key: str, doc: ImageDocument) -> dict:
        return {
            "id": key,
            "name": doc.name,
            "original_size": doc.original.size,
            "output_size": doc.output_size,
            "state": asdict(doc.state),
            "changed": doc.changed,
            "can_undo": bool(doc.undo_stack),
            "can_redo": bool(doc.redo_stack),
        }

    def list(self) -> list[dict]:
        with self.lock:
            return [self.describe(key, doc) for key, doc in self.items.items()]

    def get(self, key: str) -> ImageDocument:
        with self.lock:
            if key not in self.items:
                raise KeyError("Изображение не найдено")
            return self.items[key]

    def remove(self, key: str) -> None:
        with self.lock:
            if key not in self.items:
                raise KeyError("Изображение не найдено")
            del self.items[key]

    def update(self, key: str, payload: dict) -> dict:
        with self.lock:
            doc = self.get(key)
            action = payload.get("action")
            if action == "filter":
                name = payload.get("name")
                if name not in FILTERS:
                    raise ValueError("Неизвестный фильтр")
                doc.apply(filter_name=name)
            elif action == "adjust":
                values = {}
                for field in ("brightness", "contrast", "saturation"):
                    if field in payload:
                        value = int(payload[field])
                        if not 0 <= value <= 200:
                            raise ValueError("Регулировка должна быть от 0 до 200")
                        values[field] = value
                if not values:
                    raise ValueError("Не переданы параметры регулировки")
                doc.apply(**values)
            elif action == "rotate":
                degrees = int(payload.get("degrees", 0))
                if degrees not in (-90, 90):
                    raise ValueError("Поворот должен быть на 90 градусов")
                doc.rotate(degrees)
            elif action == "resize":
                width, height = int(payload.get("width", 0)), int(payload.get("height", 0))
                if not (1 <= width <= 20000 and 1 <= height <= 20000) or width * height > 100_000_000:
                    raise ValueError("Некорректный размер изображения")
                doc.apply(size=(width, height))
            elif action == "reset":
                doc.reset()
            elif action == "undo":
                doc.undo()
            elif action == "redo":
                doc.redo()
            else:
                raise ValueError("Неизвестное действие")
            return self.describe(key, doc)


LIBRARY = Library()


class Handler(BaseHTTPRequestHandler):
    server_version = "LumaStudio/1.0"

    def _send(self, code: int, content: bytes, content_type="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

    def _json(self, code: int, value):
        self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_UPLOAD:
            raise ValueError("Файл слишком большой (максимум 35 МБ)")
        return self.rfile.read(length)

    def _route(self):
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def _handle(self):
        path, query = self._route()
        parts = path.strip("/").split("/")
        if self.command == "GET" and path == "/api/images":
            return self._json(200, LIBRARY.list())
        if self.command == "POST" and path == "/api/images":
            name = query.get("name", ["Изображение"])[0]
            key = LIBRARY.add(self._body(), name)
            return self._json(201, LIBRARY.describe(key, LIBRARY.get(key)))
        if len(parts) >= 3 and parts[:2] == ["api", "images"]:
            key = parts[2]
            if len(parts) == 3 and self.command == "PATCH":
                payload = json.loads(self._body())
                return self._json(200, LIBRARY.update(key, payload))
            if len(parts) == 3 and self.command == "DELETE":
                LIBRARY.remove(key)
                return self._json(200, {"ok": True})
            if len(parts) == 4 and parts[3] == "preview" and self.command == "GET":
                maxw = max(1, min(1800, int(query.get("w", ["1100"])[0])))
                maxh = max(1, min(1800, int(query.get("h", ["1000"])[0])))
                with LIBRARY.lock:
                    image = LIBRARY.get(key).render((maxw, maxh))
                stream = BytesIO()
                image.save(stream, "PNG")
                return self._send(200, stream.getvalue(), "image/png")
            if len(parts) == 4 and parts[3] == "export" and self.command == "GET":
                extension = "." + query.get("format", ["png"])[0].lower().lstrip(".")
                if extension not in EXPORT_FORMATS:
                    raise ValueError("Неподдерживаемый формат")
                with LIBRARY.lock:
                    doc = LIBRARY.get(key)
                    image = doc.render()
                    name = Path(doc.name).stem + "_edited" + extension
                fmt = EXPORT_FORMATS[extension]
                if fmt in ("JPEG", "BMP") and image.mode == "RGBA":
                    background = Image.new("RGB", image.size, "white")
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                stream = BytesIO()
                opts = {"quality": 94, "optimize": True} if fmt in ("JPEG", "WEBP") else {}
                image.save(stream, fmt, **opts)
                return self._send(200, stream.getvalue(), f"image/{'jpeg' if fmt == 'JPEG' else fmt.lower()}",
                                  {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
        if self.command == "GET" and path in ("/", "/index.html", "/styles.css", "/app.js"):
            filename = "index.html" if path == "/" else path[1:]
            content = (STATIC / filename).read_bytes()
            mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                    ".js": "text/javascript; charset=utf-8"}[Path(filename).suffix]
            return self._send(200, content, mime)
        return self._json(404, {"error": "Адрес не найден"})

    def do_GET(self):
        self._safe_handle()

    def do_POST(self):
        self._safe_handle()

    def do_PATCH(self):
        self._safe_handle()

    def do_DELETE(self):
        self._safe_handle()

    def _safe_handle(self):
        try:
            self._handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A replaced preview can cancel the previous browser request.
            pass
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnidentifiedImageError) as exc:
            self._json(400 if not isinstance(exc, KeyError) else 404, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": f"Ошибка сервера: {exc}"})

    def log_message(self, format, *args):
        print("[Luma] " + format % args)


def main():
    parser = argparse.ArgumentParser(description="Local Luma Studio image editor")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    address = f"http://127.0.0.1:{server.server_port}"
    print(f"Luma Studio: {address}", flush=True)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
