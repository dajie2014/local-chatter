#!/bin/bash
# 构建「Local Chatter.app」。用法：./build.sh
# 产物：mac/build/Local Chatter.app
set -e
cd "$(dirname "$0")"

NAME="Local Chatter"
APP="build/$NAME.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "build/ModuleCache"

echo "→ 编译"
# 模块缓存默认在 /var/folders，受限环境里写不进去 —— 放到 build/ 里面
swiftc -O -module-cache-path "build/ModuleCache" \
  -o "$APP/Contents/MacOS/VoiceAssistant" VoiceAssistant/main.swift

cp Info.plist "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

if [ -f AppIcon.icns ]; then
  cp AppIcon.icns "$APP/Contents/Resources/"
fi

# 本机自用：ad-hoc 签名即可（不签名的话麦克风权限每次都会被问）
xattr -cr "$APP" 2>/dev/null || true   # 别让扩展属性挡住签名
if ! codesign --force --deep -s - "$APP" 2>/tmp/va-codesign.err; then
  sleep 0.3; xattr -cr "$APP" 2>/dev/null || true
  if ! codesign --force --deep -s - "$APP" 2>>/tmp/va-codesign.err; then
    echo "→ 签名失败（不影响运行），细节："; tail -2 /tmp/va-codesign.err
  else
    echo "→ 已签名（ad-hoc）"
  fi
else
  echo "→ 已签名（ad-hoc）"
fi

echo "→ 完成：$(pwd)/$APP"
