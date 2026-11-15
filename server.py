#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地语音助手 —— 说话 → 转写 → 本地 30B → 出声

只依赖 Python 标准库 + 本机 ffmpeg。三个上游：
  STT  http://127.0.0.1:3900/transcribe   （VoiceStudio / sherpa-onnx，快）
  LLM  http://127.0.0.1:11434/api/chat    （Ollama，qwen3.6-35b-a3b-q4:64k）
  TTS  http://127.0.0.1:3910/tts          （Qwen3-TTS 1.7B，热态约 1 秒/句）

页面：http://127.0.0.1:3921/
"""
import json
import os
import subprocess
import tempfile
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = "/tmp/voice-assistant.log"
PORT = int(os.environ.get("VOICE_ASSISTANT_PORT", "3930"))

STT_URL = "http://127.0.0.1:3900/transcribe"
TTS_URL = "http://127.0.0.1:3910/tts"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
# ★ 2026-11-12：本地模型改成可切换。key → Ollama 里真正的模型名。
#   35B 出厂默认先思考，调用时必须带 think:false，否则每轮白等十几秒。
MODELS = {
    "35b": os.environ.get("VA_MODEL_35B", "qwen3.6-35b-a3b-q4:64k"),
}
DEFAULT_MODEL_KEY = os.environ.get("VA_MODEL", "35b")
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"


def local_models():
    """本机 Ollama 里装了哪些模型 —— 每台机器不一样，让用户自己挑。"""
    try:
        with urllib.request.urlopen(OLLAMA_TAGS, timeout=3) as r:
            data = json.loads(r.read())
        names = [m.get("name") for m in data.get("models", []) if m.get("name")]
        if names:
            return names
    except Exception as e:
        log("TAGS ERR %r" % (e,))
    return list(MODELS.values())
OLLAMA_MODEL = MODELS.get(DEFAULT_MODEL_KEY, DEFAULT_MODEL_KEY)  # 兼容旧的“直接给模型名”用法
TTS_VOICE = os.environ.get("VA_VOICE", "vivian")
FFMPEG = "/opt/homebrew/bin/ffmpeg"

# ── 九个官方嗓子（2026-11-15 从 VoiceCaster 搬来，照片一起）──
# 这是 Qwen3-TTS 自带的音色名，不是我们起的昵称。
VOICES = [
    {"id": "vivian",   "name": "薇薇安",   "note": "女 · 干净"},
    {"id": "serena",   "name": "塞雷娜",   "note": "女 · 温润"},
    {"id": "uncle_fu", "name": "付叔",     "note": "中年男声"},
    {"id": "ryan",     "name": "瑞恩",     "note": "男 · 干练"},
    {"id": "aiden",    "name": "艾登",     "note": "男 · 清爽"},
    {"id": "ono_anna", "name": "小野安娜", "note": "日语女声"},
    {"id": "sohee",    "name": "素熙",     "note": "韩语女声"},
    {"id": "eric",     "name": "埃里克",   "note": "四川话男"},
    {"id": "dylan",    "name": "迪兰",     "note": "北京话男"},
]
for _v in VOICES:
    _v["avatar"] = "/characters/face_%s.png" % _v["id"]
VOICE_IDS = {v["id"] for v in VOICES}

SYSTEM = (
    "你是住在这台电脑里的本地语音助手。"
    "你在陪人说话，也帮他处理电脑上的小事。"
    "说话要短、口语、直接：一次一到两句，别列条目，别客套，别解释自己在做什么。"
    "不知道就说不知道，不要编。"
)


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


def http_post(url, body: bytes, ctype="application/json", timeout=120):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype})
    return urllib.request.urlopen(req, timeout=timeout)


def multipart(fields, files):
    """fields: dict[str,str]; files: list[(name, filename, bytes)] -> (body, ctype)"""
    b = "----voiceassistantboundary"
    out = []
    for k, v in fields.items():
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (b, k, v)).encode())
    for name, filename, data in files:
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n" % (b, name, filename)).encode())
        out.append(data + b"\r\n")
    out.append(("--%s--\r\n" % b).encode())
    return b"".join(out), "multipart/form-data; boundary=" + b


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ── 小工具 ──
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 页面改一行就要立刻看到，别被浏览器缓存住旧的
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    # ── 路由 ──
    def do_GET(self):
        # 页面地址带上 ?t=xxx 也能开 —— 改完页面靠这个绕开浏览器缓存
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                self._bytes(f.read(), "text/html; charset=utf-8")
        elif path.startswith("/characters/"):
            fn = os.path.basename(path)
            fp = os.path.join(ROOT, "characters", fn)
            if fn.endswith(".png") and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    self._bytes(f.read(), "image/png")
            else:
                self._json({"error": "not found"}, 404)
        elif self.path == "/api/ping":
            self._json({
                "ok": True,
                "model": OLLAMA_MODEL,
                "default": DEFAULT_MODEL_KEY,
                "models": [{"key": n, "model": n} for n in local_models()],
                "voice": TTS_VOICE,
                "voices": VOICES,
                "autotest": self._take_autotest(),
            })
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            if self.path == "/api/stt":
                self.handle_stt()
            elif self.path == "/api/chat":
                self.handle_chat()
            elif self.path == "/api/tts":
                self.handle_tts()
            elif self.path == "/api/log":
                self.handle_log()
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            log("ERR %s: %r" % (self.path, e))
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass

    # ── STT：原始音频字节 → 文本 ──
    def handle_stt(self):
        raw = self._read_body()
        if not raw:
            return self._json({"error": "empty audio"}, 400)
        tmpdir = tempfile.mkdtemp(prefix="va-")
        src = os.path.join(tmpdir, "in.bin")
        wav = os.path.join(tmpdir, "in.wav")
        with open(src, "wb") as f:
            f.write(raw)
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src,
                        "-ar", "16000", "-ac", "1", wav], check=True, timeout=60)
        with open(wav, "rb") as f:
            wavdata = f.read()
        body, ctype = multipart({"mode": "fast"}, [("audio", "in.wav", wavdata)])
        with http_post(STT_URL, body, ctype, timeout=120) as r:
            res = json.loads(r.read().decode())
        text = (res.get("text") or "").strip()
        log("STT %.1fs → %s" % (len(wavdata) / 32000.0, text[:40]))
        self._json({"text": text})

    # ── Chat：流式把 Ollama 的增量文本倒出去 ──
    def handle_chat(self):
        req = json.loads(self._read_body().decode())
        messages = req.get("messages") or []
        key = req.get("model") or DEFAULT_MODEL_KEY
        model = MODELS.get(key, key)
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM}] + messages,
            "stream": True,
            "options": {"temperature": 0.7, "num_predict": 300},
        }
        if key == "35b" or "35b" in model:
            payload["think"] = False  # 关掉它的默认思考，换低延迟
        body = json.dumps(payload, ensure_ascii=False).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(data: bytes):
            if not data:
                return
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()

        try:
            with http_post(OLLAMA_URL, body, "application/json", timeout=600) as r:
                for line in r:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    piece = (o.get("message") or {}).get("content", "")
                    if piece:
                        chunk(piece.encode())
                    if o.get("done"):
                        break
        except Exception as e:  # noqa: BLE001
            log("CHAT ERR %r" % (e,))
            chunk(("〔出错：%s〕" % e).encode())
        finally:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    # ── TTS：文本 → wav ──
    def _take_autotest(self):
        # 一次性开关：读到就删，免得页面崩了重载后再跑一遍死循环
        if os.path.exists("/tmp/lc-autotest"):
            try:
                os.remove("/tmp/lc-autotest")
            except Exception:
                pass
            return True
        return False

    def handle_log(self):
        # 页面崩溃前留下的脚印：前端把每一步 POST 上来，崩了就靠它定位
        try:
            raw = self._read_body().decode("utf-8", "replace")[:4000]
            item = json.loads(raw) if raw else {}
            log("PAGE %s %s" % (item.get("what", "?"), item.get("extra", "")))
        except Exception as e:
            log("PAGE (解析失败) %r" % (e,))
        self._json({"ok": True})

    def handle_tts(self):
        req = json.loads(self._read_body().decode())
        text = (req.get("text") or "").strip()
        if not text:
            return self._json({"error": "empty text"}, 400)
        body = json.dumps({
            "text": text,
            "voice": (req.get("voice") if req.get("voice") in VOICE_IDS else TTS_VOICE),
            "lang": req.get("lang") or "Chinese",
            "speed": float(req.get("speed") or 1.0),
        }).encode()
        with http_post(TTS_URL, body, "application/json", timeout=300) as r:
            data = r.read()
        self._bytes(data, "audio/wav")

    def log_message(self, fmt, *args):  # 静音 http.server 自带日志
        pass


def main():
    log("=== start %s, port %d ===" % (__import__("time").strftime("%F %T"), PORT))
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("语音助手：http://127.0.0.1:%d/" % PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
