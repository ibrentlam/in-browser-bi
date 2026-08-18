#!/usr/bin/env python3
"""Serve index.html with COOP/COEP headers (required for SharedArrayBuffer / DuckDB-Wasm multithreading)."""
import http.server, socketserver, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        super().end_headers()

    def log_message(self, fmt, *args):
        print(fmt % args)

with socketserver.TCPServer(('', PORT), Handler) as httpd:
    print(f'Serving on http://localhost:{PORT}  (Ctrl-C to stop)')
    httpd.serve_forever()
