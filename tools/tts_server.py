#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwen3-tts 1.7B (MLX) 常驻服务 —— 2026-10-31 重装（外盘 REBIRTH 不在）

POST /tts   {"text":"...","voice":"vivian","lang":"Chinese","instruct":"...","speed":1.0}
     → audio/wav
GET  /health → {"status":"ok","model":"1.7b-customvoice","voices":[...],"ready":true}

为什么常驻：模型加载约 16 秒、首句 7-8 秒（都是冷的），热起来一句快得多。
"""
import json
import os
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("QWEN_TTS_MODEL", os.path.join(_HERE, "models", "qwen3tts"))
MODEL_BASE = os.environ.get("QWEN_TTS_MODEL_BASE", os.path.join(_HERE, "models", "qwen3tts-base"))
MODEL_DESIGN = os.environ.get("QWEN_TTS_MODEL_DESIGN", os.path.join(_HERE, "models", "qwen3tts-design"))
PORT = int(os.environ.get("QWEN_TTS_PORT", "3910"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

_models = {}
_lock = threading.Lock()
_ready = False

VOICES = ["vivian", "serena", "uncle_fu", "ryan", "aiden", "ono_anna", "sohee", "eric", "dylan"]


def get_model(kind="custom"):
    """custom = 预设音色（CustomVoice）；base = 克隆（Base）。两套权重分开。"""
    if kind not in _models:
        from mlx_audio.tts.utils import load_model
        path = {"base": MODEL_BASE, "design": MODEL_DESIGN}.get(kind, MODEL)
        _models[kind] = load_model(path)
    return _models[kind]


def synth(text, voice="vivian", lang="Chinese", instruct=None, speed=1.0,
          ref_audio=None, ref_text=None, design=None):
    from mlx_audio.tts.generate import generate_audio
    d = tempfile.mkdtemp(prefix="qwen-tts-")
    if design:
        # 描述造音色（VoiceDesign）：一句话当 instruct，没有预设 voice、也不克隆
        kw = dict(
            text=text,
            model=get_model("design"),
            lang_code=lang,
            speed=speed,
            instruct=design,
            output_path=d,
            file_prefix="a",
            verbose=False,
            save=False,
        )
    elif ref_audio:
        # 克隆：只能走 Base 模型，而且必须同时给参考音的文字；
        # CustomVoice 根本不认 ref_audio（2026-10-31 实测：传了也被忽略，出的是 Vivien）
        if not (ref_text or "").strip():
            raise ValueError("克隆模式需要 ref_text（参考音里说的那句话）")
        kw = dict(
            text=text,
            model=get_model("base"),
            lang_code=lang,
            speed=speed,
            ref_audio=ref_audio,
            ref_text=ref_text,
            output_path=d,
            file_prefix="a",
            verbose=False,
            save=False,
        )
    else:
        kw = dict(
            text=text,
            model=get_model("custom"),
            voice=voice,
            lang_code=lang,
            speed=speed,
            output_path=d,
            file_prefix="a",
            verbose=False,
            save=False,
        )
        if instruct:
            kw["instruct"] = instruct
    with _lock:
        generate_audio(**kw)
    path = os.path.join(d, "a_000.wav")
    with open(path, "rb") as f:
        data = f.read()
    try:
        os.remove(path)
        os.rmdir(d)
    except OSError:
        pass
    return data


def main():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                body = json.dumps({
                    "status": "ok",
                    "model": "Qwen3-TTS-1.7B-CustomVoice-8bit",
                    "ready": _ready,
                    "voices": VOICES,
                }, ensure_ascii=False).encode()
                self._send(200, "application/json", body)
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self):
            if self.path != "/tts":
                self._send(404, "text/plain", b"not found")
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                req = json.loads(self.rfile.read(n) or b"{}")
                text = (req.get("text") or "").strip()
                if not text:
                    self._send(400, "text/plain", b"empty text")
                    return
                wav = synth(
                    text,
                    voice=req.get("voice") or "vivian",
                    lang=req.get("lang") or "Chinese",
                    instruct=req.get("instruct"),
                    speed=float(req.get("speed") or 1.0),
                    ref_audio=req.get("ref_audio"),
                    ref_text=req.get("ref_text"),
                    design=req.get("design"),
                )
                self._send(200, "audio/wav", wav)
            except Exception as e:  # noqa: BLE001
                self._send(500, "text/plain", str(e).encode("utf-8"))

    print("loading model...", flush=True)
    get_model()
    print("warming up...", flush=True)
    try:
        synth("你好。")
    except Exception as e:  # noqa: BLE001
        print("warmup failed: %s" % e, flush=True)
    global _ready
    _ready = True
    print("ready on 127.0.0.1:%d" % PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
