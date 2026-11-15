#!/bin/bash
# 本地语音助手启动器（页面 http://127.0.0.1:3930/）
cd "$(dirname "$0")" || exit 1

# 预热本地 30B：不预热的话第一句要等约 9 秒（模型冷加载）
curl -s -m 180 http://127.0.0.1:11434/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6-35b-a3b-q4:64k","think":false,"messages":[{"role":"user","content":"hi"}],"stream":false,"options":{"num_predict":1}}' \
  >/dev/null 2>&1 &

exec python3 server.py
