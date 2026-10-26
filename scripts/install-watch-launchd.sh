#!/usr/bin/env sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LABEL=com.ai-review-agent.watch-promotion
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HOUR=${AI_REVIEW_WATCH_HOUR:-6}
MINUTE=${AI_REVIEW_WATCH_MINUTE:-0}
EVERY_HOURS=${AI_REVIEW_WATCH_EVERY_HOURS:-6}
DOMAIN="gui/$(id -u)"

case "$EVERY_HOURS" in
  1|2|3|4|6|8|12|24) ;;
  *) echo "AI_REVIEW_WATCH_EVERY_HOURS must divide 24 (1, 2, 3, 4, 6, 8, 12, 24)." >&2; exit 2 ;;
esac

HOURS=""
CALENDAR=""
i=0
while [ "$i" -lt 24 ]; do
  if [ $(( (i - HOUR + 24) % EVERY_HOURS )) -eq 0 ]; then
    HOURS="$HOURS $(printf '%02d:%02d' "$i" "$MINUTE")"
    CALENDAR="$CALENDAR    <dict><key>Hour</key><integer>$i</integer><key>Minute</key><integer>$MINUTE</integer></dict>
"
  fi
  i=$((i + 1))
done

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI not found on PATH; run this from the shell where 'claude' works." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PACKAGE_ROOT/outputs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>$PACKAGE_ROOT/scripts/watch-promotion.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$PATH</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>
$CALENDAR  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$PACKAGE_ROOT/outputs/watch.log</string>
  <key>StandardErrorPath</key><string>$PACKAGE_ROOT/outputs/watch.log</string>
</dict>
</plist>
EOF

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $LABEL (daily at$HOURS). Log: $PACKAGE_ROOT/outputs/watch.log"
