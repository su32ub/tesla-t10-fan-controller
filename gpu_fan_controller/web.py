import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
CONTROL_PATH = re.compile(r"^/api/gpus/(\d+)/control$")


class FanHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller):
        super().__init__(address, FanRequestHandler)
        self.controller = controller


class FanRequestHandler(BaseHTTPRequestHandler):
    server_version = f"GpuFanController/{__version__}"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._send_json(200, self.server.controller.snapshot())
            return
        if path == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/":
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._send_file(STATIC_DIR / "app.js", "text/javascript; charset=utf-8")
            return
        if path == "/styles.css":
            self._send_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
            return
        self._send_json(404, {"error": "资源不存在"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/curve/reset":
            try:
                self.server.controller.reset_auto_curve()
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except Exception as exc:
                log.exception("恢复默认曲线时发生未预期错误")
                self._send_json(500, {"error": f"内部错误：{exc}"})
                return
            self._send_json(200, self.server.controller.snapshot())
            return
        match = CONTROL_PATH.match(path)
        if match is None:
            self._send_json(404, {"error": "资源不存在"})
            return
        if not self._content_type_is_json():
            self._send_json(415, {"error": "Content-Type 必须是 application/json"})
            return
        try:
            payload = self._read_json()
            mode = payload.get("mode")
            if not isinstance(mode, str):
                raise ValueError("mode 必须是字符串")
            self.server.controller.set_control(
                int(match.group(1)),
                mode,
                payload.get("duty_percent"),
            )
        except KeyError as exc:
            self._send_json(404, {"error": str(exc.args[0])})
            return
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:  # 避免控制线程异常导致连接被直接丢弃
            log.exception("处理控制请求时发生未预期错误")
            self._send_json(500, {"error": f"内部错误：{exc}"})
            return
        self._send_json(200, self.server.controller.snapshot())

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/curve":
            self._send_json(404, {"error": "资源不存在"})
            return
        if not self._content_type_is_json():
            self._send_json(415, {"error": "Content-Type 必须是 application/json"})
            return
        try:
            payload = self._read_json()
            curve = payload.get("curve")
            if not isinstance(curve, list):
                raise ValueError("curve 必须是 [温度, 占空比] 数组")
            self.server.controller.set_auto_curve(curve)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:
            log.exception("处理曲线更新请求时发生未预期错误")
            self._send_json(500, {"error": f"内部错误：{exc}"})
            return
        self._send_json(200, self.server.controller.snapshot())

    def _content_type_is_json(self) -> bool:
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return content_type == "application/json"

    def _read_json(self) -> dict:
        length_text = self.headers.get("Content-Length")
        if length_text is None:
            raise ValueError("缺少 Content-Length")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if not 0 < length <= 8192:
            raise ValueError("请求体长度无效")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json(404, {"error": "资源不存在"})
            return
        self._send_body(200, body, content_type)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_body(status, body, "application/json; charset=utf-8")

    def _send_body(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._send_common_headers()
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # 前端每次刷新都会取消上一次请求，属于正常现象
            pass

    def _send_common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'",
        )

    def log_message(self, format: str, *args) -> None:
        # 面板每 1 秒轮询一次，访问日志只在 DEBUG 级别输出
        log.debug("%s - %s", self.address_string(), format % args)


def serve(controller, host: str, port: int) -> None:
    server = FanHttpServer((host, port), controller)
    try:
        controller.start()
        log.info("监听 http://%s:%d", host, port)
        server.serve_forever(poll_interval=0.5)
    finally:
        controller.stop()
        server.server_close()
