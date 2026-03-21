"""
Web server — serves the Renko chart HTML to your phone.
Runs on Render.com as a Web Service.
"""

import os
import http.server
import socketserver
from pathlib import Path

# Render sets the PORT environment variable
PORT = int(os.environ.get("PORT", 10000))

# Chart file location
STATIC_DIR = Path("/opt/render/project/src/static")
HTML_FILE  = STATIC_DIR / "index.html"

# Placeholder HTML shown while chart is being built
BUILDING_HTML = b"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="15">
<title>LTCUSD Chart Loading...</title>
</head>
<body style="background:#050810;color:#38bdf8;font-family:monospace;
  padding:40px;text-align:center;">
<h2 style="font-size:1.5rem;letter-spacing:3px;">#LTCUSD RENKO CHART</h2>
<br>
<p style="color:#f5c842;">Building chart from tick data...</p>
<p style="color:#3d5a72;font-size:.8rem;">
  The feeder is collecting Binance ticks.<br>
  The chart will appear here after 500 ticks (~3-5 minutes).<br>
  This page refreshes automatically every 15 seconds.
</p>
<br>
<div style="width:200px;height:4px;background:#131f30;margin:0 auto;border-radius:2px;overflow:hidden;">
  <div style="width:60%;height:100%;background:linear-gradient(90deg,#f5c842,#38bdf8);
    animation:slide 1.5s ease-in-out infinite alternate;">
  </div>
</div>
<style>
@keyframes slide{from{margin-left:0}to{margin-left:40%}}
</style>
</body>
</html>"""


class ChartHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        # Serve the chart for any request to / or /index.html
        if self.path in ('/', '/index.html', '/m25_m31_live.html'):
            if HTML_FILE.exists():
                try:
                    with open(HTML_FILE, 'rb') as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type',   'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(content)))
                    # No-cache so phone always gets the latest rebuilt chart
                    self.send_header('Cache-Control',  'no-cache, no-store, must-revalidate')
                    self.send_header('Pragma',         'no-cache')
                    self.end_headers()
                    self.wfile.write(content)
                except Exception as e:
                    self._serve_error(str(e))
            else:
                # Chart not ready yet — show building page
                self.send_response(200)
                self.send_header('Content-Type',   'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(BUILDING_HTML)))
                self.end_headers()
                self.wfile.write(BUILDING_HTML)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not found')

    def _serve_error(self, msg):
        body = f"Error: {msg}".encode()
        self.send_response(500)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Only log actual chart requests
        if args and ('200' in str(args[1]) or '404' in str(args[1])):
            print(f"[WEB] {self.address_string()} - {args[0]}", flush=True)


def main():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[WEB] Chart server starting on port {PORT}", flush=True)
    print(f"[WEB] Serving: {HTML_FILE}", flush=True)

    # Allow reuse of address
    socketserver.TCPServer.allow_reuse_address = True

    with socketserver.TCPServer(("0.0.0.0", PORT), ChartHandler) as httpd:
        print(f"[WEB] Ready — your chart URL will be shown in Render dashboard", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
