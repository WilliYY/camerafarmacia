import argparse
import hmac
import json
import secrets
import socket
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .backend import NvrHealthBridge, build_dashboard_payload
from .network_diagnostics import WindowsNetworkDiagnostics


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HEALTH_PATH = PROJECT_ROOT / "sistema" / "logs" / "health_status.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SESSION_COOKIE = "wimi_session"

STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class AnalyticsHttpServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(
        self,
        address,
        handler,
        bridge,
        static_dir=STATIC_DIR,
        network_diagnostics=None,
    ):
        super().__init__(address, handler)
        self.bridge = bridge
        self.network_diagnostics = network_diagnostics or WindowsNetworkDiagnostics()
        self.static_dir = Path(static_dir).resolve()
        self.session_token = secrets.token_urlsafe(32)
        host, port = self.server_address[:2]
        self.panel_url = f"http://{host}:{port}"
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


class AnalyticsRequestHandler(BaseHTTPRequestHandler):
    server_version = "WimiAnalytics/0.1"
    sys_version = ""

    def log_message(self, format_string, *args):
        return

    def _host_allowed(self):
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _origin_allowed(self):
        origin = self.headers.get("Origin")
        return origin is None or origin in self.server.allowed_origins

    def _session_allowed(self):
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return False
        morsel = cookie.get(SESSION_COOKIE)
        return bool(
            morsel
            and hmac.compare_digest(morsel.value, self.server.session_token)
        )

    def _security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-src http://127.0.0.1:1984 http://localhost:1984; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )

    def _send_bytes(self, status, body, content_type, set_session=False, head_only=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if set_session:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={self.server.session_token}; HttpOnly; SameSite=Strict; Path=/",
            )
        self._security_headers()
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_json(self, status, payload, head_only=False):
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", head_only=head_only)

    def _send_error_json(self, status, code, head_only=False):
        self._send_json(status, {"error": code}, head_only=head_only)

    def _route(self, head_only=False):
        if not self._host_allowed():
            self._send_error_json(HTTPStatus.MISDIRECTED_REQUEST, "host_not_allowed", head_only)
            return
        if not self._origin_allowed():
            self._send_error_json(HTTPStatus.FORBIDDEN, "origin_not_allowed", head_only)
            return

        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {"service": "wimi-analytics", "status": "ready"},
                head_only,
            )
            return

        if path.startswith("/api/"):
            if not self._session_allowed():
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "session_required", head_only)
                return
            payload = build_dashboard_payload(
                self.server.bridge,
                self.server.panel_url,
                network=self.server.network_diagnostics.read(),
            )
            if path == "/api/v1/overview":
                self._send_json(HTTPStatus.OK, payload, head_only)
            elif path == "/api/v1/nvr/health":
                self._send_json(HTTPStatus.OK, payload["nvr"], head_only)
            elif path == "/api/v1/modules":
                self._send_json(HTTPStatus.OK, {"modules": payload["modules"]}, head_only)
            elif path == "/api/v1/network/status":
                self._send_json(HTTPStatus.OK, payload["network"], head_only)
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "route_not_found", head_only)
            return

        static_route = STATIC_ROUTES.get(path)
        if static_route is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "route_not_found", head_only)
            return
        file_name, content_type = static_route
        static_path = (self.server.static_dir / file_name).resolve()
        if static_path.parent != self.server.static_dir or not static_path.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "asset_not_found", head_only)
            return
        try:
            body = static_path.read_bytes()
        except OSError:
            self._send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "asset_unavailable", head_only)
            return
        self._send_bytes(
            HTTPStatus.OK,
            body,
            content_type,
            set_session=path in ("/", "/index.html"),
            head_only=head_only,
        )

    def do_GET(self):
        self._route(head_only=False)

    def do_HEAD(self):
        self._route(head_only=True)

    def do_POST(self):
        self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "read_only_service")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def create_server(
    host=DEFAULT_HOST,
    port=DEFAULT_PORT,
    bridge=None,
    static_dir=STATIC_DIR,
    network_diagnostics=None,
):
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("WIMI Analytics deve escutar somente na interface local")
    bridge = bridge or NvrHealthBridge(DEFAULT_HEALTH_PATH)
    return AnalyticsHttpServer(
        (host, int(port)),
        AnalyticsRequestHandler,
        bridge,
        static_dir,
        network_diagnostics,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Painel local WIMI Analytics")
    parser.add_argument("--host", default=DEFAULT_HOST, choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--health-path", type=Path, default=DEFAULT_HEALTH_PATH)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port deve estar entre 1024 e 65535")

    server = create_server(
        host=args.host,
        port=args.port,
        bridge=NvrHealthBridge(args.health_path),
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
