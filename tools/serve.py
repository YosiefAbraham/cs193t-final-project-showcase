#!/usr/bin/env python3
"""Dev server for the showcase/ directory (stdlib only).

Usage: python3 tools/serve.py [port]    (default port 4321, bound to 127.0.0.1)
"""

import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, "..", "showcase"))
DEFAULT_PORT = 4321

# Must match showcase/.htaccess exactly.
# upgrade-insecure-requests is omitted here on purpose: Safari applies it to http://localhost and
# rewrites every asset to https://localhost, which breaks the page. Production (.htaccess) keeps it.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "font-src 'self'; img-src 'self'; connect-src 'self'; manifest-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; object-src 'none'; "
    "require-trusted-types-for 'script'; trusted-types 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    "X-Frame-Options": "SAMEORIGIN",
    "Cross-Origin-Opener-Policy": "same-origin",
}

MIME_TYPES = {
    ".webp": "image/webp",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
}


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **MIME_TYPES}

    def end_headers(self):
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def list_directory(self, path):
        # Directory listings are disabled.
        self.send_error(404, "Not Found")
        return None

    def log_request(self, code="-", size="-"):
        # One line per request: status, path, and which browser asked (useful for cross-browser bugs).
        ua = self.headers.get("User-Agent", "")
        browser = next((b for b in ("Edg", "Chrome", "Firefox", "Safari") if b in ua), "other")
        print(f"{code} {self.command} {self.path} [{browser}]", flush=True)

    def log_message(self, format, *args):
        pass


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    handler = partial(Handler, directory=ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Serving {ROOT} at http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
