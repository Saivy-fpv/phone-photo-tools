#!/usr/bin/env python3
"""fotodrop – get photos from your phone into the current folder.

    fotodrop.py [--port 8000] [--ip IP]

Scan the QR code in the terminal, take a photo, and it lands in the folder
fotodrop was started in (foto_20261109_143012.jpg). Ctrl+C quits.

Python standard library only; the QR code needs `qrencode`.
The port must be open in your firewall (e.g. `sudo ufw allow 8000/tcp`).
"""
import argparse
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

lock = threading.Lock()
cfg = {"dir": None, "token": "", "count": 0}


def save_photo(origname, data):
    d = cfg["dir"]
    ext = Path(origname).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext):
        ext = ".jpg"
    stem = time.strftime("foto_%Y%m%d_%H%M%S")
    with lock:
        f, n = d / f"{stem}{ext}", 1
        while f.exists():
            n += 1
            f = d / f"{stem}_{n}{ext}"
        tmp = f.with_name(f".{f.name}.part")
        tmp.write_bytes(data)
        tmp.replace(f)
        cfg["count"] += 1
    return f.name


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/plain; charset=utf-8"):
        body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == f"/{cfg['token']}/":
            self.send(200, PHONE_HTML, "text/html; charset=utf-8")
        else:
            self.send(404, "not found")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != f"/{cfg['token']}/upload":
            return self.send(404, "not found")
        size = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(size)
        # do not save an interrupted transfer as a broken image
        if not size or len(data) < size:
            return self.send(400, "incomplete")
        q = dict(urllib.parse.parse_qsl(u.query))
        fname = save_photo(q.get("name", ""), data)
        print(f"  + {fname}", flush=True)
        self.send(200, fname)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind calls socket.getfqdn(), which can hang without DNS
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_name, self.server_port = "fotodrop", self.server_address[1]

    def handle_error(self, request, client_address):
        print(f"  ! {sys.exc_info()[1]}", flush=True)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="Get photos from your phone into the current folder")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ip", help="IP address for the QR code (default: detected automatically)")
    a = ap.parse_args()

    cfg["dir"] = Path.cwd()
    cfg["token"] = secrets.token_urlsafe(6)
    try:
        srv = Server(("0.0.0.0", a.port), H)
    except OSError as e:
        sys.exit(f"Port {a.port} not available: {e} (pick another one with --port and open it in the firewall)")

    url = f"http://{a.ip or lan_ip()}:{a.port}/{cfg['token']}/"
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "1", url])
    print(f"Folder: {cfg['dir']}\nPhone:  {url}\n(Ctrl+C to quit)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    print(f"\n{cfg['count']} photo(s) saved.")


PHONE_HTML = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>fotodrop</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#111;color:#eee;text-align:center}
.btn{display:block;width:100%;box-sizing:border-box;padding:48px 16px;border-radius:12px;border:0;font-size:24px;font-weight:600;background:#2a6;color:#fff}
#st{margin:24px 0;font-size:20px}
#re{display:none;padding:32px 16px;font-size:20px;background:#a52}
</style></head><body>
<label class=btn>📷 Photo<input type=file accept="image/*" capture=environment hidden id=cam></label>
<div id=st>No photos yet</div>
<button class=btn id=re>Send again</button>
<script>
const $=id=>document.getElementById(id);
let queue=[],done=0,busy=false;
// photos from the camera prompt only exist in the browser: keep them if sending fails
async function run(){
  if(busy)return;
  busy=true;$('re').style.display='none';
  while(queue.length){
    $('st').textContent=`Sending … (${done} saved, ${queue.length} left)`;
    try{
      const r=await fetch('upload?name='+encodeURIComponent(queue[0].name),{method:'POST',body:queue[0]});
      if(!r.ok)throw new Error(r.status);
      queue.shift();done++;
    }catch(e){
      $('st').textContent=`❌ Sending failed (${done} saved, ${queue.length} left)`;
      $('re').style.display='block';busy=false;return;
    }
  }
  $('st').textContent=`✅ ${done} saved`;
  busy=false;
}
$('cam').onchange=e=>{queue.push(...e.target.files);e.target.value='';run()};
$('re').onclick=run;
</script></body></html>"""

if __name__ == "__main__":
    main()
