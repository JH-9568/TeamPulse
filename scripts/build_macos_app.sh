#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_PATH="$DIST_DIR/OpenBrief.app"

rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

cat > "$APP_PATH/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>OpenBrief</string>
  <key>CFBundleIdentifier</key>
  <string>dev.openbrief.launcher</string>
  <key>CFBundleName</key>
  <string>OpenBrief</string>
  <key>CFBundleDisplayName</key>
  <string>OpenBrief</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.2</string>
  <key>CFBundleVersion</key>
  <string>0.1.2</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
</dict>
</plist>
PLIST

cat > "$APP_PATH/Contents/MacOS/OpenBrief" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

if ! command -v openbrief >/dev/null 2>&1; then
  osascript -e 'display dialog "OpenBrief CLI is not installed. Run: pipx install \"git+https://github.com/JH-9568/OpenBrief.git\"" buttons {"OK"} default button "OK"'
  exit 127
fi

openbrief start --daemon
open "http://127.0.0.1:8000/dashboard"
LAUNCHER

chmod +x "$APP_PATH/Contents/MacOS/OpenBrief"

echo "Built $APP_PATH"
echo "This unsigned app requires OpenBrief to be installed first:"
echo '  pipx install "git+https://github.com/JH-9568/OpenBrief.git"'
