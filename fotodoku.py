#!/usr/bin/env python3
"""fotodoku – file photos from your phone straight into a documented folder
(commissioning jobs, repairs, build logs …).

    fotodoku.py [DIRECTORY] [--port 8000]

Laptop: http://localhost:PORT/   → QR code, folder picker, overview
Phone:  scan the QR code → title + note + photo(s) → upload

Photos are named <Title>_001.jpg, <Title>_002.jpg, … (numbered per title).
The folder gets a comments.md:

    # Title
    ## 001
    Note
    ![Title_001.jpg](Title_001.jpg)

An existing comments.md and existing photos are read in, numbering continues.
The phone address contains a random token, so other devices on the same
network cannot browse or upload without the QR code.
Python standard library only; the QR code needs `qrencode`.
"""
import argparse
import json
import re
import secrets
import shutil
import socket
import subprocess
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MD = "comments.md"
IMG_RE = re.compile(r"!\[[^\]]*\]\(<?([^)>]+)>?\)")
lock = threading.Lock()
cfg = {"dir": None, "url": "", "qr": "", "ip": "", "token": ""}


# ---------------------------------------------------------------- comments.md

def parse_md(path):
    """→ (text before the first title, [{title, intro, entries:[{num, body}]}])"""
    pre, titles, t, e = [], [], None, None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                t, e = {"title": line[2:].strip(), "intro": [], "entries": []}, None
                titles.append(t)
            elif line.startswith("## ") and t is not None:
                e = {"num": line[3:].strip(), "body": []}
                t["entries"].append(e)
            elif e is not None:
                e["body"].append(line)
            elif t is not None:
                t["intro"].append(line)
            else:
                pre.append(line)
    return pre, titles


def write_md(path, pre, titles):
    out = []
    if "\n".join(pre).strip():
        out += ["\n".join(pre).strip(), ""]
    for t in titles:
        out += [f"# {t['title']}", ""]
        if "\n".join(t["intro"]).strip():
            out += ["\n".join(t["intro"]).strip(), ""]
        for e in t["entries"]:
            out += [f"## {e['num']}", "\n".join(e["body"]).strip(), ""]
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text("\n".join(out), encoding="utf-8")
    tmp.replace(path)


def safe(title):
    s = re.sub(r"\s+", "_", title.strip())
    return re.sub(r"[^\w\-]", "", s) or "photo"


def num_of(e):
    m = re.match(r"\d+", e["num"])
    return int(m.group()) if m else 0


def next_num(d, title, t):
    nums = [num_of(e) for e in t["entries"]] if t else []
    pat = re.compile(rf"^{re.escape(safe(title))}_(\d+)\.\w+$")
    nums += [int(m.group(1)) for f in d.iterdir() if (m := pat.match(f.name))]
    return max(nums, default=0) + 1


def save_photo(title, note, origname, data):
    d = Path(cfg["dir"])
    ext = Path(origname).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext):
        ext = ".jpg"
    with lock:
        pre, titles = parse_md(d / MD)
        t = next((x for x in titles if x["title"] == title), None)
        n = next_num(d, title, t)
        if t is None:
            t = {"title": title, "intro": [], "entries": []}
            titles.append(t)
        fname = f"{safe(title)}_{n:03d}{ext}"
        (d / fname).write_bytes(data)
        body = [*note.strip().splitlines(), ""] if note.strip() else []
        t["entries"].append({"num": f"{n:03d}", "body": body + [f"![{fname}]({fname})"]})
        write_md(d / MD, pre, titles)
    return fname


def state():
    d = Path(cfg["dir"])
    _, titles = parse_md(d / MD)
    res = []
    for t in titles:
        entries = []
        for e in t["entries"]:
            body = "\n".join(e["body"])
            files = [f for f in IMG_RE.findall(body) if (d / Path(f).name).is_file()]
            entries.append({"num": e["num"], "text": IMG_RE.sub("", body).strip(),
                            "files": [Path(f).name for f in files]})
        res.append({"title": t["title"], "entries": entries, "next": next_num(d, t["title"], t)})
    return {"dir": str(d), "md": (d / MD).exists(), "titles": res}


# ---------------------------------------------------------------- Server

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def local(self):
        ip = self.client_address[0]
        return ip in ("127.0.0.1", "::1") or ip == cfg["ip"]

    def route(self):
        """→ (path without token prefix, query) or None if the request is not allowed.
        The laptop itself may use everything; other devices need the token."""
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        prefix = f"/{cfg['token']}/"
        if u.path.startswith(prefix):
            return "/" + u.path[len(prefix):], q
        if self.local():
            return u.path, q
        return None

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = (json.dumps(body, ensure_ascii=False) if "json" in ctype else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def do_GET(self):
        r = self.route()
        if r is None:
            return self.send(404, {"error": "not found"})
        path, q = r
        html = "text/html; charset=utf-8"
        if path == "/":
            self.send(200, PC_HTML if self.local() else PHONE_HTML, html)
        elif path == "/m":
            self.send(200, PHONE_HTML, html)
        elif path == "/api/state":
            s = state()
            if self.local():
                s.update(url=cfg["url"], qr=cfg["qr"])
            self.send(200, s)
        elif path == "/api/next":
            title = q.get("title", "").strip()
            _, titles = parse_md(Path(cfg["dir"]) / MD)
            t = next((x for x in titles if x["title"] == title), None)
            self.send(200, {"next": next_num(Path(cfg["dir"]), title, t), "new": t is None})
        elif path == "/api/browse" and self.local():
            p = Path(q.get("path") or cfg["dir"]).expanduser().resolve()
            if not p.is_dir():
                return self.send(404, {"error": "not a directory"})
            dirs = sorted(x.name for x in p.iterdir() if x.is_dir() and not x.name.startswith("."))
            self.send(200, {"path": str(p), "parent": str(p.parent), "dirs": dirs})
        elif path.startswith("/photo/"):
            f = Path(cfg["dir"]) / Path(urllib.parse.unquote(path[7:])).name
            if not f.is_file():
                return self.send(404, {"error": "not found"})
            ext = f.suffix.lower().lstrip(".")
            self.send(200, f.read_bytes(), f"image/{'jpeg' if ext == 'jpg' else ext}")
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        r = self.route()
        if r is None:
            return self.send(404, {"error": "not found"})
        path, q = r
        if path == "/upload":
            title = q.get("title", "").strip()
            if not title:
                return self.send(400, {"error": "title missing"})
            fname = save_photo(title, q.get("note", ""), q.get("name", ""), self.body())
            print(f"  + {fname}")
            self.send(200, {"file": fname})
        elif path == "/api/dir" and self.local():
            req = json.loads(self.body() or "{}")
            p = Path(req.get("path", "")).expanduser().resolve()
            if not p.is_dir():
                if not req.get("create"):
                    return self.send(404, {"error": "does not exist"})
                p.mkdir(parents=True)
            with lock:
                cfg["dir"] = str(p)
            print(f"Folder: {p}")
            self.send(200, {"dir": str(p)})
        else:
            self.send(404, {"error": "not found"})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind calls socket.getfqdn(), which can hang without DNS
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_name, self.server_port = "fotodoku", self.server_address[1]


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
    ap = argparse.ArgumentParser(description="File photos from your phone into a folder, with comments.md")
    ap.add_argument("dir", nargs="?", default=".", help="target folder (default: current directory)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ip", help="IP address for the QR code (default: detected automatically)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the overview in the browser")
    a = ap.parse_args()

    d = Path(a.dir).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    cfg["dir"] = str(d)
    cfg["ip"] = a.ip or lan_ip()
    cfg["token"] = secrets.token_urlsafe(6)
    cfg["url"] = f"http://{cfg['ip']}:{a.port}/{cfg['token']}/m"
    if shutil.which("qrencode"):
        cfg["qr"] = subprocess.run(["qrencode", "-t", "SVG", "-m", "1", "-o", "-", cfg["url"]],
                                   capture_output=True, text=True).stdout
        subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "1", cfg["url"]])

    srv = Server(("0.0.0.0", a.port), H)
    print(f"Folder: {d}\nPhone:  {cfg['url']}\nLaptop: http://localhost:{a.port}/\n(Ctrl+C to quit)")
    if not a.no_browser:
        webbrowser.open(f"http://localhost:{a.port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------- HTML
# The phone page uses relative URLs, so every request keeps the token prefix.

PHONE_HTML = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>fotodoku</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#111;color:#eee}
h2{margin:0 0 4px}small{color:#999;word-break:break-all}
label.l{display:block;margin:16px 0 6px;font-weight:600}
input,textarea{width:100%;box-sizing:border-box;font-size:18px;padding:12px;border-radius:8px;border:1px solid #444;background:#222;color:#eee}
textarea{min-height:110px;font-family:inherit}
.row{display:flex;gap:8px;margin-top:16px}
.btn{flex:1;display:block;text-align:center;padding:16px;border-radius:8px;border:0;font-size:18px;font-weight:600;background:#357;color:#fff}
.go{background:#2a6;width:100%;margin-top:12px}.go:disabled{background:#444}
#prev{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}#prev img{height:80px;border-radius:6px}
#log div{padding:6px 0;border-bottom:1px solid #333;font-size:15px}
</style></head><body>
<h2>fotodoku</h2><small id=dir></small>
<label class=l for=title>Step / title</label>
<input id=title list=titles autocomplete=off autocapitalize=sentences><datalist id=titles></datalist>
<small id=next></small>
<label class=l for=note>Note</label><textarea id=note></textarea>
<div class=row>
 <label class=btn>📷 Camera<input type=file accept="image/*" capture=environment hidden id=cam></label>
 <label class=btn>🖼 Gallery<input type=file accept="image/*" multiple hidden id=gal></label>
</div>
<div id=prev></div>
<button class="btn go" id=go disabled>Upload</button>
<div id=log></div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let files=[];
try{$('title').value=localStorage.getItem('title')||''}catch(e){}
async function refresh(){
  const s=await (await fetch('api/state')).json();
  $('dir').textContent=s.dir;
  $('titles').innerHTML=s.titles.map(t=>`<option value="${esc(t.title)}">`).join('');
  next();
}
let tm;
function next(){clearTimeout(tm);tm=setTimeout(async()=>{
  const t=$('title').value.trim();
  if(!t){$('next').textContent='';return}
  const r=await (await fetch('api/next?title='+encodeURIComponent(t))).json();
  $('next').textContent='Next no.: '+String(r.next).padStart(3,'0')+(r.new?' (new title)':'');
},250)}
function add(ev){
  files.push(...ev.target.files);ev.target.value='';
  $('prev').innerHTML=files.map(f=>`<img src="${URL.createObjectURL(f)}">`).join('');
  $('go').disabled=!files.length;$('go').textContent=`Upload (${files.length})`;
}
$('cam').onchange=add;$('gal').onchange=add;$('title').oninput=next;
$('go').onclick=async()=>{
  const title=$('title').value.trim(),note=$('note').value;
  if(!title){alert('Title missing');return}
  $('go').disabled=true;
  try{localStorage.setItem('title',title)}catch(e){}
  for(const f of files){
    const q=new URLSearchParams({title,note,name:f.name});
    try{
      const r=await fetch('upload?'+q,{method:'POST',body:f});const j=await r.json();
      $('log').insertAdjacentHTML('afterbegin',`<div>${r.ok?'✅ '+esc(j.file):'❌ '+esc(j.error)}</div>`);
    }catch(e){$('log').insertAdjacentHTML('afterbegin',`<div>❌ ${esc(f.name)}: ${esc(String(e))}</div>`)}
  }
  files=[];$('prev').innerHTML='';$('note').value='';$('go').textContent='Upload';
  refresh();
};
refresh();
</script></body></html>"""

PC_HTML = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>fotodoku</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f4f4f2;color:#222}
header{display:flex;gap:24px;align-items:center;padding:16px 24px;background:#fff;border-bottom:1px solid #ddd;flex-wrap:wrap}
#qr svg{width:170px;height:170px;display:block}
#url{font-family:monospace;font-size:15px}
.dir{flex:1;min-width:320px}.dir input{width:100%;box-sizing:border-box;font-size:15px;padding:8px;font-family:monospace}
.dir button{padding:6px 12px;margin-top:6px}
#sub{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px;max-height:120px;overflow:auto}
#sub a{background:#eee;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:13px}
main{padding:8px 24px 40px}
h2{margin:28px 0 8px;border-bottom:2px solid #ccc;padding-bottom:4px}
.e{display:flex;gap:14px;background:#fff;border-radius:8px;padding:10px;margin:8px 0}
.e .n{font-weight:700;font-family:monospace;min-width:40px}
.e img{height:130px;border-radius:4px;cursor:zoom-in}
.e .t{white-space:pre-wrap;flex:1}
.muted{color:#888}
#big{position:fixed;inset:0;background:#000d;display:none;align-items:center;justify-content:center;cursor:zoom-out}
#big img{max-width:95vw;max-height:95vh}
</style></head><body>
<header>
 <div id=qr></div>
 <div><b>Connect your phone</b><br><span id=url></span><br><small class=muted>same Wi-Fi, scan the QR code</small></div>
 <div class=dir><b>Folder</b>
  <input id=path><br>
  <button id=use>Use this folder</button> <button id=up>⬆ Parent</button>
  <div id=sub></div>
 </div>
</header>
<main id=list></main>
<div id=big><img></div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let last='',parent='';
async function browse(p){
  const r=await fetch('/api/browse?path='+encodeURIComponent(p));
  if(!r.ok)return;const b=await r.json();
  $('path').value=b.path;parent=b.parent;
  $('sub').innerHTML=b.dirs.map(d=>`<a data-d="${esc(d)}">📁 ${esc(d)}</a>`).join('')||'<span class=muted>no subfolders</span>';
}
$('sub').onclick=e=>{const d=e.target.dataset.d;if(d)browse($('path').value.replace(/\/$/,'')+'/'+d)};
$('up').onclick=()=>browse(parent);
$('path').onkeydown=e=>{if(e.key==='Enter')browse($('path').value)};
$('use').onclick=async()=>{
  const path=$('path').value;
  let r=await fetch('/api/dir',{method:'POST',body:JSON.stringify({path})});
  if(r.status===404&&confirm('Folder does not exist. Create it?'))
    r=await fetch('/api/dir',{method:'POST',body:JSON.stringify({path,create:true})});
  if(r.ok){last='';load()}
};
async function load(){
  const s=await (await fetch('/api/state')).json();
  const key=JSON.stringify(s);if(key===last)return;
  const first=!last;last=key;
  if(first){$('qr').innerHTML=s.qr;$('url').textContent=s.url;browse(s.dir)}
  document.title='fotodoku – '+s.dir.split('/').pop();
  $('list').innerHTML=`<p class=muted>${esc(s.dir)}/comments.md ${s.md?'':'(not created yet)'}</p>`+
   s.titles.map(t=>`<h2>${esc(t.title)} <small class=muted>(${t.entries.length}, next ${String(t.next).padStart(3,'0')})</small></h2>`+
    t.entries.map(e=>`<div class=e><div class=n>${esc(e.num)}</div>`+
      e.files.map(f=>`<img src="/photo/${encodeURIComponent(f)}" loading=lazy>`).join('')+
      `<div class=t>${esc(e.text)||'<span class=muted>–</span>'}</div></div>`).join('')).join('');
}
$('list').onclick=e=>{if(e.target.tagName==='IMG'){$('big').firstChild.src=e.target.src;$('big').style.display='flex'}};
$('big').onclick=()=>$('big').style.display='none';
load();setInterval(load,2000);
</script></body></html>"""

if __name__ == "__main__":
    main()
