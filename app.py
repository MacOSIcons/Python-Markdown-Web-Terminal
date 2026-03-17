#!/usr/bin/env python3
import atexit
import os
import pty
import secrets
import select
import signal
import struct
import subprocess
import threading
import time
import urllib.parse
from functools import lru_cache

import fcntl
import markdown
import termios
from flask import Flask, abort, jsonify, redirect, render_template_string, request, session
from markupsafe import Markup

try:
    import bleach
except Exception:
    bleach = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_ROOT = os.path.realpath(os.environ.get("CONTENT_ROOT", BASE_DIR))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "webterm_session")
TERMINAL_IDLE_SECONDS = int(os.environ.get("TERMINAL_IDLE_SECONDS", "3600"))
READ_CHUNK = 65536
DEFAULT_ROWS = 40
DEFAULT_COLS = 120

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_NAME=SESSION_COOKIE_NAME,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

MARKDOWN_EXTENSIONS = [
    "extra",
    "admonition",
    "attr_list",
    "def_list",
    "fenced_code",
    "footnotes",
    "md_in_html",
    "meta",
    "nl2br",
    "sane_lists",
    "smarty",
    "tables",
    "toc",
    "codehilite",
    "pymdownx.arithmatex",
    "pymdownx.betterem",
    "pymdownx.caret",
    "pymdownx.details",
    "pymdownx.highlight",
    "pymdownx.inlinehilite",
    "pymdownx.keys",
    "pymdownx.mark",
    "pymdownx.smartsymbols",
    "pymdownx.snippets",
    "pymdownx.superfences",
    "pymdownx.tabbed",
    "pymdownx.tasklist",
    "pymdownx.tilde",
]

MARKDOWN_EXTENSION_CONFIGS = {
    "codehilite": {"guess_lang": False, "use_pygments": True, "noclasses": False},
    "toc": {"permalink": True, "baselevel": 1},
    "pymdownx.highlight": {"anchor_linenums": False, "guess_lang": False},
    "pymdownx.superfences": {},
    "pymdownx.tabbed": {"alternate_style": True},
    "pymdownx.tasklist": {"custom_checkbox": True},
    "pymdownx.arithmatex": {
        "generic": True,
        "block_tag": "div",
        "inline_tag": "span",
        "smart_dollar": True,
    },
}

TEMPLATE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/animejs@3.2.1/lib/anime.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/particles.js@2.0.0/particles.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/vanilla-tilt@1.8.1/dist/vanilla-tilt.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm/css/xterm.css">
<script src="https://cdn.jsdelivr.net/npm/xterm/lib/xterm.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit/lib/xterm-addon-fit.js"></script>
<script>
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\(', '\\)']],
    displayMath: [['$$', '$$'], ['\\[', '\\]']],
    processEscapes: true,
    processEnvironments: true,
    packages: {'[+]': ['noerrors', 'noundefined']}
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  },
  svg: {
    fontCache: 'global'
  }
};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
:root {
  --bg: #050505;
  --panel: rgba(255,255,255,0.06);
  --panel-2: rgba(255,255,255,0.10);
  --border: rgba(255,255,255,0.12);
  --cyan: #22d3ee;
  --cyan-2: #00ffea;
  --yellow: #eab308;
  --text: #e0f7ff;
  --shadow: 0 25px 50px -12px rgb(0 0 0 / 0.5);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: #000;
  color: var(--text);
  font-family: 'Share Tech Mono', monospace;
  overflow-x: hidden;
}
a { color: inherit; }
button { font: inherit; }
#particles-js {
  position: fixed;
  inset: 0;
  z-index: -1;
  background: radial-gradient(circle at center, #0a0a1a 0%, #000 72%);
}
.page-shell {
  max-width: 84rem;
  margin: 0 auto;
  padding: 2rem 1.25rem 4rem;
}
.upper-shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 1.25rem;
}
.path-bar {
  width: 100%;
  display: flex;
  align-items: center;
  gap: .75rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
  padding: 1rem 1.25rem;
  border-radius: 1rem;
  background: rgba(255,255,255,0.05);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255,255,255,0.10);
}
.crumb-link {
  color: var(--text);
  text-decoration: none;
  transition: color .2s ease, opacity .2s ease;
  cursor: pointer;
}
.crumb-link:hover {
  color: var(--cyan);
  opacity: 1;
}
.crumb-current {
  color: rgba(255,255,255,.92);
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: .75rem;
  margin-bottom: 1rem;
}
.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: .75rem;
  flex-wrap: wrap;
}
.badge {
  display: inline-flex;
  align-items: center;
  gap: .5rem;
  padding: .75rem 1rem;
  border-radius: 9999px;
  background: rgba(34,211,238,0.08);
  border: 1px solid rgba(34,211,238,0.25);
  color: #a5f3fc;
  font-size: .9rem;
}
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: .5rem;
  padding: .85rem 1.1rem;
  border-radius: 1rem;
  text-decoration: none;
  color: var(--text);
  border: 1px solid rgba(255,255,255,.12);
  background: rgba(255,255,255,.05);
  cursor: pointer;
  transition: transform .2s ease, background .2s ease, border-color .2s ease;
}
.btn:hover {
  background: rgba(34,211,238,.12);
  border-color: rgba(34,211,238,.35);
}
.btn:active {
  transform: scale(.98);
}
.btn-primary {
  color: #a5f3fc;
  border-color: rgba(34,211,238,.35);
}
.page-title {
  font-size: clamp(2rem, 4vw, 3rem);
  font-weight: 700;
  margin: .2rem 0 1.25rem;
  text-align: center;
}
.neon-text {
  text-shadow: 0 0 15px #00ffea, 0 0 30px #ff00aa, 0 0 40px #eab308;
  animation: neon-pulse 3s infinite alternate;
}
@keyframes neon-pulse {
  from { text-shadow: 0 0 15px #00ffea, 0 0 30px #ff00aa; }
  to { text-shadow: 0 0 30px #00ffea, 0 0 60px #ff00aa, 0 0 80px #eab308; }
}
.grid-wrap {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 1.25rem;
}
.glass-card {
  width: 100%;
  background: var(--panel);
  backdrop-filter: blur(24px);
  border: 1px solid var(--border);
  transition: all .35s cubic-bezier(.23,1,.32,1);
  color: inherit;
  cursor: pointer;
}
.glass-card:hover {
  background: var(--panel-2);
  border-color: rgba(0,245,255,.42);
  box-shadow: var(--shadow);
  transform: translateY(-3px) scale(1.015);
}
.card-pad {
  border-radius: 1.5rem;
  padding: 1.75rem;
  text-align: center;
}
.emoji {
  font-size: 4rem;
  transition: transform .6s cubic-bezier(.34,1.56,.64,1);
}
.glass-card:hover .emoji {
  transform: scale(1.12) rotate(6deg);
}
.meta-line {
  display: flex;
  justify-content: center;
  gap: .75rem;
  flex-wrap: wrap;
  margin-top: .75rem;
  color: rgba(255,255,255,.64);
  font-size: .8rem;
}
.glass-content {
  background: rgba(255,255,255,.06);
  backdrop-filter: blur(24px);
  border: 1px solid rgba(0,255,234,.25);
  border-radius: 1.5rem;
  padding: clamp(1.2rem, 2vw, 2.5rem);
  box-shadow: 0 10px 35px rgba(0,0,0,.25);
}
.prose {
  max-width: none;
  color: var(--text);
  line-height: 1.82;
  font-size: 1.06rem;
  word-break: break-word;
}
.prose :first-child { margin-top: 0; }
.prose :last-child { margin-bottom: 0; }
.prose h1, .prose h2, .prose h3, .prose h4, .prose h5, .prose h6 {
  color: var(--cyan-2);
  line-height: 1.35;
  margin-top: 1.7em;
  margin-bottom: .7em;
  scroll-margin-top: 6rem;
}
.prose h1 { font-size: 2.1rem; }
.prose h2 { font-size: 1.7rem; }
.prose h3 { font-size: 1.4rem; }
.prose p, .prose ul, .prose ol, .prose blockquote, .prose table, .prose .arithmatex, .prose pre {
  margin: 1em 0;
}
.prose ul, .prose ol { padding-left: 1.5rem; }
.prose li + li { margin-top: .4rem; }
.prose a {
  color: #93c5fd;
  text-decoration: none;
  border-bottom: 1px dashed rgba(147,197,253,.45);
}
.prose a:hover {
  color: #bfdbfe;
  border-bottom-color: rgba(191,219,254,.75);
}
.prose strong { color: #fff; }
.prose hr {
  border: none;
  border-top: 1px solid rgba(255,255,255,.12);
  margin: 2rem 0;
}
.prose blockquote {
  padding: 1rem 1.1rem;
  border-left: 4px solid rgba(34,211,238,.4);
  background: rgba(255,255,255,.04);
  border-radius: 0 1rem 1rem 0;
  color: #d9faff;
}
.prose code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .95em;
}
.prose p code, .prose li code, .prose td code, .prose blockquote code {
  background: rgba(255,255,255,.08);
  border: 1px solid rgba(255,255,255,.1);
  padding: .18rem .42rem;
  border-radius: .45rem;
}
.prose pre {
  overflow: auto;
  padding: 1rem;
  border-radius: 1rem;
  border: 1px solid rgba(255,255,255,.12);
  background: rgba(5,10,16,.92);
}
.prose pre code {
  background: transparent;
  border: none;
  padding: 0;
}
.prose table {
  width: 100%;
  border-collapse: collapse;
  overflow: hidden;
  display: block;
}
.prose thead, .prose tbody, .prose tr {
  width: 100%;
}
.prose th, .prose td {
  border: 1px solid rgba(255,255,255,.12);
  padding: .75rem .8rem;
  text-align: left;
}
.prose th {
  color: #a5f3fc;
  background: rgba(34,211,238,.08);
}
.prose img {
  max-width: 100%;
  border-radius: 1rem;
  border: 1px solid rgba(255,255,255,.12);
}
.prose .toc {
  background: rgba(255,255,255,.04);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 1rem;
  padding: 1rem 1.1rem;
}
.prose .arithmatex {
  overflow-x: auto;
}
.prose .task-list-item {
  list-style: none;
}
.prose .task-list-control {
  margin-right: .55rem;
}
.empty-state {
  padding: 2rem 1.25rem;
  text-align: center;
  color: rgba(255,255,255,.78);
  background: rgba(255,255,255,.04);
  border: 1px dashed rgba(255,255,255,.12);
  border-radius: 1.25rem;
}
.shell {
  max-width: 84rem;
  margin: 1.25rem auto 3rem;
  padding: 0 1.25rem;
}
.term-title {
  font-size: clamp(1.8rem, 4vw, 3rem);
  text-align: center;
  margin: 0 0 1rem;
}
.term-box {
  background: rgba(10,10,20,.75);
  border-radius: 1.25rem;
  padding: 1rem;
  border: 1px solid rgba(0,255,255,.15);
  backdrop-filter: blur(20px);
  box-shadow: var(--shadow);
}
#terminal {
  height: 32rem;
}
.status-line {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: .75rem;
  margin-bottom: .75rem;
  color: rgba(255,255,255,.7);
  font-size: .9rem;
}
.error-box {
  margin-top: 1rem;
  padding: .9rem 1rem;
  border-radius: 1rem;
  background: rgba(255, 70, 70, .12);
  border: 1px solid rgba(255, 70, 70, .28);
  color: #fecaca;
  display: none;
}
.error-box.show {
  display: block;
}
@media (max-width: 768px) {
  .page-shell { padding-left: .85rem; padding-right: .85rem; }
  .shell { padding-left: .85rem; padding-right: .85rem; }
  #terminal { height: 24rem; }
}
</style>
</head>
<body>
<div id="particles-js"></div>
<div class="page-shell">
  <div class="upper-shell">
    <div id="upper">{{ upper_html|safe }}</div>
    <div id="upper-error" class="error-box"></div>
  </div>
</div>
<div class="shell">
  <h1 class="term-title neon-text">WEB TERMINAL</h1>
  <div class="term-box">
    <div class="status-line">
      <div>内容浏览根目录：{{ content_root }}</div>
      <div id="term-status">终端已连接</div>
    </div>
    <div id="terminal"></div>
  </div>
</div>
<script>
const state = {
  currentPath: "{{ initial_path }}"
};

particlesJS("particles-js", {
  particles: {
    number: { value: 85 },
    color: { value: ["#00f5ff","#a855f7","#67e8f9"] },
    shape: { type: "circle" },
    opacity: { value: 0.65, random: true },
    size: { value: 2.8, random: true },
    line_linked: { enable: true, distance: 140, color: "#00f5ff", opacity: 0.18, width: 1.2 },
    move: { enable: true, speed: 1.1 }
  },
  interactivity: {
    events: {
      onhover: { enable: true, mode: "repulse" },
      onclick: { enable: true, mode: "push" }
    }
  }
});

function showUpperError(msg) {
  const el = document.getElementById("upper-error");
  el.textContent = msg || "";
  el.classList.toggle("show", !!msg);
}

function initUpper() {
  if (document.querySelector('.grid-wrap')) {
    anime({
      targets: '.glass-card',
      translateY: [40, 0],
      opacity: [0, 1],
      duration: 750,
      easing: 'easeOutExpo',
      delay: anime.stagger(55)
    });
    VanillaTilt.init(document.querySelectorAll(".glass-card"), {
      max: 12,
      speed: 450,
      glare: true,
      "max-glare": 0.25
    });
  }
  if (window.hljs) {
    document.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
  }
  if (window.MathJax && window.MathJax.typesetPromise) {
    MathJax.typesetPromise([document.getElementById('upper')]).catch(() => {});
  }
}

async function loadFragment(path) {
  try {
    const r = await fetch('/upper_fragment' + (path === '/' ? '' : path), { cache: "no-store" });
    const html = await r.text();
    document.getElementById("upper").innerHTML = html;
    state.currentPath = path;
    showUpperError("");
    initUpper();
  } catch (e) {
    showUpperError("上方内容加载失败");
  }
}

function refreshUpper() {
  loadFragment(state.currentPath || "/");
}

window.addEventListener("DOMContentLoaded", () => {
  initUpper();
});

async function postJSON(url, payload) {
  const r = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload || {})
  });
  if (!r.ok) {
    throw new Error("request failed");
  }
  return r.json();
}

const term = new Terminal({
  cursorBlink: true,
  fontSize: 14,
  fontFamily: "Menlo, Consolas, monospace",
  theme: {
    background: "#050505",
    foreground: "#e0f7ff",
    cursor: "#00ffff"
  },
  convertEol: false,
  allowTransparency: true,
  scrollback: 5000
});

const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.open(document.getElementById("terminal"));
fitAddon.fit();

function setStatus(text) {
  const el = document.getElementById("term-status");
  if (el) el.textContent = text;
}

async function resizeTerm() {
  try {
    await postJSON("/resize", { rows: term.rows, cols: term.cols });
  } catch (e) {
  }
}

resizeTerm();

window.addEventListener("resize", () => {
  fitAddon.fit();
  resizeTerm();
});

let writeQueue = "";
let writeBusy = false;

async function flushWriteQueue() {
  if (writeBusy || !writeQueue) return;
  writeBusy = true;
  const payload = writeQueue;
  writeQueue = "";
  try {
    await postJSON("/write", { data: payload });
  } catch (e) {
    writeQueue = payload + writeQueue;
  } finally {
    writeBusy = false;
    if (writeQueue) {
      setTimeout(flushWriteQueue, 10);
    }
  }
}

term.onData(data => {
  writeQueue += data;
  flushWriteQueue();
});

let pollBusy = false;

async function poll() {
  if (!pollBusy) {
    pollBusy = true;
    try {
      const r = await fetch("/read", { cache: "no-store" });
      if (r.ok) {
        const j = await r.json();
        if (j.output) term.write(j.output);
        setStatus("终端已连接");
      } else {
        setStatus("终端连接异常");
      }
    } catch (e) {
      setStatus("终端连接异常");
    } finally {
      pollBusy = false;
    }
  }
  requestAnimationFrame(poll);
}

poll();
</script>
</body>
</html>
"""

UPPER_MD_TEMPLATE = r"""
<nav class="path-bar">
  {% for crumb in breadcrumbs %}
    {% if not loop.first %}<span class="text-white/40">›</span>{% endif %}
    {% if crumb.url %}
      <a href="#" onclick="loadFragment('{{ crumb.url }}'); return false;" class="crumb-link">
        {% if loop.first %}🏠 {% endif %}{{ crumb.label }}
      </a>
    {% else %}
      <span class="crumb-current">{% if loop.first %}🏠 {% endif %}{{ crumb.label }}</span>
    {% endif %}
  {% endfor %}
</nav>
<div class="toolbar">
  <div class="toolbar-left">
    <span class="badge">📝 Markdown</span>
    <span class="badge">📄 {{ filename }}</span>
    <span class="badge">📏 {{ line_count }} 行</span>
    <span class="badge">🔢 {{ char_count }} 字符</span>
  </div>
  <div class="toolbar-right">
    <button onclick="refreshUpper()" class="btn btn-primary">🔄 刷新内容</button>
    {% if parent_url %}
      <button onclick="loadFragment('{{ parent_url }}')" class="btn">⬅ 返回目录</button>
    {% endif %}
  </div>
</div>
<div class="glass-content">
  <article class="prose" id="math-content">{{ html_content|safe }}</article>
</div>
"""

UPPER_DIR_TEMPLATE = r"""
<nav class="path-bar">
  {% for crumb in breadcrumbs %}
    {% if not loop.first %}<span class="text-white/40">›</span>{% endif %}
    {% if crumb.url %}
      <a href="#" onclick="loadFragment('{{ crumb.url }}'); return false;" class="crumb-link">
        {% if loop.first %}🏠 {% endif %}{{ crumb.label }}
      </a>
    {% else %}
      <span class="crumb-current">{% if loop.first %}🏠 {% endif %}{{ crumb.label }}</span>
    {% endif %}
  {% endfor %}
</nav>
<div class="toolbar">
  <div class="toolbar-left">
    <span class="badge">📁 目录</span>
    <span class="badge">📦 {{ dir_count }} 个目录</span>
    <span class="badge">📝 {{ md_count }} 篇文章</span>
    <span class="badge">📚 共 {{ total_count }} 项</span>
  </div>
  <div class="toolbar-right">
    <button onclick="refreshUpper()" class="btn btn-primary">🔄 刷新内容</button>
    {% if parent_url %}
      <button onclick="loadFragment('{{ parent_url }}')" class="btn">⬆ 上一级</button>
    {% endif %}
  </div>
</div>
<h1 class="page-title neon-text">{{ title }}</h1>
{% if items %}
  <div class="grid-wrap">
    {% for item in items %}
      <a href="#" onclick="loadFragment('{{ item.url }}'); return false;" class="glass-card card-pad block" data-tilt>
        <div class="emoji">{{ item.emoji }}</div>
        <div class="text-2xl font-bold neon-text mt-6">{{ item.name }}</div>
        <div class="meta-line">
          <span>{{ item.kind_label }}</span>
          {% if item.size_label %}<span>{{ item.size_label }}</span>{% endif %}
          {% if item.mtime_label %}<span>{{ item.mtime_label }}</span>{% endif %}
        </div>
      </a>
    {% endfor %}
  </div>
{% else %}
  <div class="empty-state">这个目录是空的</div>
{% endif %}
"""

UPPER_NOT_FOUND_TEMPLATE = r"""
<nav class="path-bar">
  <span class="crumb-current">🏠 首页</span>
  <span class="text-white/40">›</span>
  <span class="crumb-current">404</span>
</nav>
<div class="empty-state">内容不存在</div>
"""

class TerminalSession:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.master_fd, slave_fd = pty.openpty()
        env = os.environ.copy()
        env.update(
            {
                "TERM": "xterm-256color",
                "COLORTERM": "truecolor",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "en_US.UTF-8",
            }
        )
        self.proc = subprocess.Popen(
            ["bash", "-i"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=root_dir,
            env=env,
            preexec_fn=os.setsid,
            close_fds=True,
        )
        os.close(slave_fd)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, os.O_NONBLOCK)
        self.lock = threading.RLock()
        self.last_used = time.time()

    def touch(self):
        self.last_used = time.time()

    def is_alive(self):
        return self.proc.poll() is None

    def write(self, data: str):
        with self.lock:
            if not self.is_alive():
                raise RuntimeError("terminal exited")
            os.write(self.master_fd, data.encode())
            self.touch()

    def read(self) -> str:
        with self.lock:
            chunks = []
            while True:
                r, _, _ = select.select([self.master_fd], [], [], 0)
                if self.master_fd not in r:
                    break
                try:
                    chunk = os.read(self.master_fd, READ_CHUNK)
                except BlockingIOError:
                    break
                if not chunk:
                    break
                chunks.append(chunk.decode(errors="ignore"))
            self.touch()
            return "".join(chunks)

    def resize(self, rows: int, cols: int):
        with self.lock:
            packed = struct.pack("hhhh", int(rows), int(cols), 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, packed)
            self.touch()

    def terminate(self):
        with self.lock:
            try:
                if self.is_alive():
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=1.0)
            except Exception:
                try:
                    if self.is_alive():
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            try:
                os.close(self.master_fd)
            except Exception:
                pass

class TerminalManager:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.sessions = {}
        self.lock = threading.RLock()
        self.gc_thread = threading.Thread(target=self.gc_loop, daemon=True)
        self.gc_thread.start()

    def get(self, sid: str) -> TerminalSession:
        with self.lock:
            term = self.sessions.get(sid)
            if term is None or not term.is_alive():
                if term is not None:
                    term.terminate()
                term = TerminalSession(self.root_dir)
                term.resize(DEFAULT_ROWS, DEFAULT_COLS)
                self.sessions[sid] = term
            term.touch()
            return term

    def gc_loop(self):
        while True:
            time.sleep(30)
            cutoff = time.time() - TERMINAL_IDLE_SECONDS
            dead = []
            with self.lock:
                for sid, term in list(self.sessions.items()):
                    if term.last_used < cutoff or not term.is_alive():
                        dead.append(term)
                        self.sessions.pop(sid, None)
            for term in dead:
                try:
                    term.terminate()
                except Exception:
                    pass

    def shutdown(self):
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for term in sessions:
            try:
                term.terminate()
            except Exception:
                pass

terminal_manager = TerminalManager(CONTENT_ROOT)
atexit.register(terminal_manager.shutdown)

def ensure_session_id():
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_urlsafe(24)
        session["sid"] = sid
        session.permanent = True
    return sid

def current_terminal():
    return terminal_manager.get(ensure_session_id())

def normalize_rel_path(path: str):
    x = (path or "").replace("\\", "/").strip()
    if x in ("", "/"):
        return ""
    x = x.strip("/")
    parts = []
    for p in x.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
            continue
        parts.append(p)
    return "/".join(parts)

def get_dir_info(subpath=''):
    rel = normalize_rel_path(subpath)
    full_path = os.path.realpath(os.path.join(CONTENT_ROOT, rel))
    if full_path != CONTENT_ROOT and not full_path.startswith(CONTENT_ROOT + os.sep):
        abort(404)
    rel_path = os.path.relpath(full_path, CONTENT_ROOT).replace(os.sep, '/').rstrip('/')
    if rel_path == '.':
        rel_path = ''
    return full_path, rel_path

def build_breadcrumbs(rel_path, is_file=False):
    breadcrumbs = [{'label': '首页', 'url': '/' if rel_path else None, 'is_current': rel_path == ''}]
    if not rel_path:
        return breadcrumbs
    parts = [p for p in rel_path.split('/') if p]
    current_parts = []
    for i, part in enumerate(parts):
        current_parts.append(part)
        is_last = i == len(parts) - 1
        label = part
        if is_last and is_file and label.lower().endswith('.md'):
            label = label[:-3]
        url = None if is_last else '/' + '/'.join(urllib.parse.quote(p) for p in current_parts) + '/'
        breadcrumbs.append({'label': label, 'url': url, 'is_current': is_last})
    return breadcrumbs

def build_parent_url(rel_path: str):
    if not rel_path:
        return None
    parts = [p for p in rel_path.split('/') if p]
    if len(parts) <= 1:
        return '/'
    parent = '/'.join(urllib.parse.quote(p) for p in parts[:-1])
    return '/' + parent + '/'

def format_bytes(size: int):
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{int(size)}B"

def format_mtime(ts: float):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

@lru_cache(maxsize=256)
def read_markdown_file(file_path: str, mtime_ns: int, size: int):
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

def render_markdown_content(md_text: str):
    engine = markdown.Markdown(
        extensions=MARKDOWN_EXTENSIONS,
        extension_configs=MARKDOWN_EXTENSION_CONFIGS,
        output_format="html5",
    )
    html_content = engine.convert(md_text)
    if bleach is not None:
        allowed_tags = set(bleach.sanitizer.ALLOWED_TAGS) | {
            "p", "pre", "code", "span", "div", "img", "h1", "h2", "h3", "h4", "h5", "h6",
            "table", "thead", "tbody", "tr", "th", "td", "hr", "br", "details", "summary",
            "input", "del", "ins", "mark", "sup", "sub"
        }
        allowed_attrs = {
            "*": ["class", "id", "title", "aria-hidden"],
            "a": ["href", "title", "target", "rel"],
            "img": ["src", "alt", "title"],
            "input": ["type", "checked", "disabled"],
        }
        html_content = bleach.clean(
            html_content,
            tags=list(allowed_tags),
            attributes=allowed_attrs,
            protocols=["http", "https", "mailto"],
            strip=True,
        )
        html_content = bleach.linkify(html_content)
    return html_content

def build_directory_items(full_path: str, rel_path: str):
    items = []
    names = sorted(os.listdir(full_path), key=lambda name: (not os.path.isdir(os.path.join(full_path, name)), name.lower()))
    base_prefix = '/' + (rel_path + '/' if rel_path else '')
    for name in names:
        if name.startswith("."):
            continue
        item_full = os.path.join(full_path, name)
        try:
            st = os.stat(item_full)
        except Exception:
            continue

        if os.path.isdir(item_full):
            items.append(
                {
                    "type": "dir",
                    "name": name,
                    "url": base_prefix + urllib.parse.quote(name) + '/',
                    "emoji": "📁",
                    "kind_label": "目录",
                    "size_label": None,
                    "mtime_label": format_mtime(st.st_mtime),
                }
            )
        elif name.lower().endswith(".md"):
            items.append(
                {
                    "type": "md",
                    "name": name[:-3],
                    "url": base_prefix + urllib.parse.quote(name),
                    "emoji": "📝",
                    "kind_label": "文章",
                    "size_label": format_bytes(st.st_size),
                    "mtime_label": format_mtime(st.st_mtime),
                }
            )
    return items

def get_upper_fragment(subpath):
    try:
        full_path, rel_path = get_dir_info(subpath)
    except Exception:
        return render_template_string(UPPER_NOT_FOUND_TEMPLATE), 404

    breadcrumbs = build_breadcrumbs(
        rel_path,
        is_file=full_path.lower().endswith('.md') if os.path.isfile(full_path) else False
    )

    if os.path.isfile(full_path) and full_path.lower().endswith('.md'):
        st = os.stat(full_path)
        md_content = read_markdown_file(full_path, st.st_mtime_ns, st.st_size)
        html_content = render_markdown_content(md_content)
        return render_template_string(
            UPPER_MD_TEMPLATE,
            breadcrumbs=breadcrumbs,
            html_content=Markup(html_content),
            filename=os.path.basename(full_path),
            line_count=md_content.count("\n") + (1 if md_content else 0),
            char_count=len(md_content),
            parent_url=build_parent_url(rel_path),
        )

    if os.path.isdir(full_path):
        items = build_directory_items(full_path, rel_path)
        dirs = sum(1 for x in items if x["type"] == "dir")
        mds = sum(1 for x in items if x["type"] == "md")
        title = rel_path.split('/')[-1] if rel_path else '首页'
        return render_template_string(
            UPPER_DIR_TEMPLATE,
            breadcrumbs=breadcrumbs,
            title=title,
            items=items,
            dir_count=dirs,
            md_count=mds,
            total_count=len(items),
            parent_url=build_parent_url(rel_path),
        )

    return render_template_string(UPPER_NOT_FOUND_TEMPLATE), 404

@app.before_request
def bootstrap_session():
    ensure_session_id()

@app.route('/upper_fragment', defaults={'subpath': ''})
@app.route('/upper_fragment/<path:subpath>')
def upper_fragment(subpath):
    return get_upper_fragment(subpath)

@app.get("/")
def index():
    upper_html = get_upper_fragment("")
    if isinstance(upper_html, tuple):
        upper_html = upper_html[0]
    return render_template_string(
        TEMPLATE,
        title="小磷碎碎念",
        upper_html=Markup(upper_html),
        initial_path="/",
        content_root=CONTENT_ROOT,
    )

@app.route('/<path:subpath>')
def force_home(subpath):
    return redirect('/')

@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})

@app.post("/write")
def write():
    payload = request.get_json(silent=True) or {}
    data = str(payload.get("data", ""))
    if not data:
        return jsonify({"ok": True})
    try:
        current_terminal().write(data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/read")
def read():
    try:
        output = current_terminal().read()
        return jsonify({"ok": True, "output": output})
    except Exception as e:
        return jsonify({"ok": False, "output": "", "error": str(e)}), 500

@app.post("/resize")
def resize():
    payload = request.get_json(silent=True) or {}
    rows = max(10, min(200, int(payload.get("rows", DEFAULT_ROWS))))
    cols = max(20, min(500, int(payload.get("cols", DEFAULT_COLS))))
    try:
        current_terminal().resize(rows, cols)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
