#!/bin/bash
# Install the household-finance launchd jobs on the Mac Mini.
# Idempotent: unloads and reloads if already present.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
PY="$(command -v python3)"
LOGS="$ROOT/state/logs"

mkdir -p "$AGENTS" "$LOGS"

# write_plist <label> <executable> <arg-or-empty> <hour> <minute> <weekday-or-empty> <keepalive:0|1>
write_plist() {
  local label="$1" exe="$2" arg="$3" hour="$4" minute="$5" weekday="$6" keepalive="$7"
  local plist="$AGENTS/$label.plist"

  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "  <key>Label</key><string>$label</string>"
    echo '  <key>ProgramArguments</key><array>'
    echo "    <string>$exe</string>"
    [ -n "$arg" ] && echo "    <string>$arg</string>"
    echo '  </array>'
    echo "  <key>WorkingDirectory</key><string>$ROOT</string>"
    echo '  <key>EnvironmentVariables</key><dict>'
    echo '    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>'
    echo '    <key>PYTHONUNBUFFERED</key><string>1</string>'
    echo "    <key>TELEGRAM_BOT_TOKEN</key><string>${TELEGRAM_BOT_TOKEN:-}</string>"
    echo '  </dict>'
    if [ "$keepalive" = "1" ]; then
      echo '  <key>KeepAlive</key><true/>'
      echo '  <key>RunAtLoad</key><true/>'
    else
      echo '  <key>StartCalendarInterval</key><dict>'
      echo "    <key>Hour</key><integer>$hour</integer>"
      echo "    <key>Minute</key><integer>$minute</integer>"
      [ -n "$weekday" ] && echo "    <key>Weekday</key><integer>$weekday</integer>"
      echo '  </dict>'
    fi
    echo "  <key>StandardOutPath</key><string>$LOGS/$label.log</string>"
    echo "  <key>StandardErrorPath</key><string>$LOGS/$label.err.log</string>"
    echo '</dict></plist>'
  } > "$plist"

  launchctl unload "$plist" 2>/dev/null || true
  launchctl load "$plist"
  echo "loaded $label"
}

# --- wrapper scripts (bash, with shebangs, executed directly by launchd) ---

cat > "$ROOT/scripts/morning.sh" <<EOF
#!/bin/bash
cd "$ROOT"
[ -f "$LOGS/engine-runs.log" ] && tail -n 4000 "$LOGS/engine-runs.log" > "$LOGS/engine-runs.log.tmp" && mv "$LOGS/engine-runs.log.tmp" "$LOGS/engine-runs.log"
"$PY" scripts/engine.py --refresh >>"$LOGS/engine-runs.log" 2>&1 || "$PY" scripts/engine.py >>"$LOGS/engine-runs.log" 2>&1
exec "$PY" scripts/notify.py
EOF
chmod +x "$ROOT/scripts/morning.sh"

cat > "$ROOT/scripts/sunday.sh" <<EOF
#!/bin/bash
cd "$ROOT"
"$PY" scripts/engine.py --refresh >>"$LOGS/engine-runs.log" 2>&1 || true
"$PY" scripts/weekly.py >>"$LOGS/engine-runs.log" 2>&1
exec "$PY" scripts/notify.py --weekly
EOF
chmod +x "$ROOT/scripts/sunday.sh"

cat > "$ROOT/scripts/watch.sh" <<EOF
#!/bin/bash
cd "$ROOT"
"$PY" scripts/engine.py --refresh >>"$LOGS/engine-runs.log" 2>&1 || true
exec "$PY" scripts/alerts.py
EOF
chmod +x "$ROOT/scripts/watch.sh"

cat > "$ROOT/scripts/evening.sh" <<EOF
#!/bin/bash
cd "$ROOT"
"$PY" scripts/engine.py >>"$LOGS/engine-runs.log" 2>&1 || true
exec "$PY" scripts/notify.py --evening
EOF
chmod +x "$ROOT/scripts/evening.sh"

# Morning brief, 7:00am daily.
write_plist "com.housewright.morning" "$ROOT/scripts/morning.sh" "" 7 0 "" 0

# Evening wrap, 8:30pm daily.
write_plist "com.housewright.evening" "$ROOT/scripts/evening.sh" "" 20 30 "" 0

# Sunday review, 5:00pm. Weekday 0 = Sunday.
write_plist "com.housewright.sunday" "$ROOT/scripts/sunday.sh" "" 17 0 0 0

# Always-on dashboard + read-only endpoint for the tailnet.
write_plist "com.housewright.serve" "$PY" "$ROOT/scripts/serve.py" 0 0 "" 1

# Always-on Telegram bot (long polling; exits fast and relaunches if token unset).
write_plist "com.housewright.bot" "$PY" "$ROOT/scripts/telegram_bot.py" 0 0 "" 1

# Watch job: refresh + spending alerts. StartInterval, every 3 hours.
# (alerts.py itself stays quiet 9pm-7am; night runs just refresh data.)
WATCH_PLIST="$AGENTS/com.housewright.watch.plist"
{
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
  echo '<plist version="1.0"><dict>'
  echo '  <key>Label</key><string>com.housewright.watch</string>'
  echo '  <key>ProgramArguments</key><array>'
  echo "    <string>$ROOT/scripts/watch.sh</string>"
  echo '  </array>'
  echo "  <key>WorkingDirectory</key><string>$ROOT</string>"
  echo '  <key>EnvironmentVariables</key><dict>'
  echo '    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>'
  echo '    <key>PYTHONUNBUFFERED</key><string>1</string>'
  echo "    <key>TELEGRAM_BOT_TOKEN</key><string>${TELEGRAM_BOT_TOKEN:-}</string>"
  echo '  </dict>'
  echo '  <key>StartInterval</key><integer>10800</integer>'
  echo "  <key>StandardOutPath</key><string>$LOGS/com.housewright.watch.log</string>"
  echo "  <key>StandardErrorPath</key><string>$LOGS/com.housewright.watch.err.log</string>"
  echo '</dict></plist>'
} > "$WATCH_PLIST"
launchctl unload "$WATCH_PLIST" 2>/dev/null || true
launchctl load "$WATCH_PLIST"
echo "loaded com.housewright.watch"

# Energy watch: poll the Shelly plugs every 10 minutes. Read only, LAN only.
ENERGY_PLIST="$AGENTS/com.housewright.energy.plist"
{
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
  echo '<plist version="1.0"><dict>'
  echo '  <key>Label</key><string>com.housewright.energy</string>'
  echo '  <key>ProgramArguments</key><array>'
  echo "    <string>$PY</string>"
  echo "    <string>$ROOT/scripts/energy.py</string>"
  echo '    <string>--watch</string>'
  echo '  </array>'
  echo "  <key>WorkingDirectory</key><string>$ROOT</string>"
  echo '  <key>EnvironmentVariables</key><dict>'
  echo '    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>'
  echo '    <key>PYTHONUNBUFFERED</key><string>1</string>'
  echo "    <key>TELEGRAM_BOT_TOKEN</key><string>${TELEGRAM_BOT_TOKEN:-}</string>"
  echo '  </dict>'
  echo '  <key>StartInterval</key><integer>600</integer>'
  echo "  <key>StandardOutPath</key><string>$LOGS/com.housewright.energy.log</string>"
  echo "  <key>StandardErrorPath</key><string>$LOGS/com.housewright.energy.err.log</string>"
  echo '</dict></plist>'
} > "$ENERGY_PLIST"
launchctl unload "$ENERGY_PLIST" 2>/dev/null || true
launchctl load "$ENERGY_PLIST"
echo "loaded com.housewright.energy"

# Family events: scan Gmail for kids/school/household events, file onto the
# shared Family calendar. Every 30 minutes.
FAMILY_PLIST="$AGENTS/com.housewright.familyevents.plist"
{
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
  echo '<plist version="1.0"><dict>'
  echo '  <key>Label</key><string>com.housewright.familyevents</string>'
  echo '  <key>ProgramArguments</key><array>'
  echo "    <string>$PY</string>"
  echo "    <string>$ROOT/scripts/family_events.py</string>"
  echo '  </array>'
  echo "  <key>WorkingDirectory</key><string>$ROOT</string>"
  echo '  <key>EnvironmentVariables</key><dict>'
  echo '    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>'
  echo '    <key>PYTHONUNBUFFERED</key><string>1</string>'
  echo "    <key>TELEGRAM_BOT_TOKEN</key><string>${TELEGRAM_BOT_TOKEN:-}</string>"
  echo '  </dict>'
  echo '  <key>StartInterval</key><integer>1800</integer>'
  echo "  <key>StandardOutPath</key><string>$LOGS/com.housewright.familyevents.log</string>"
  echo "  <key>StandardErrorPath</key><string>$LOGS/com.housewright.familyevents.err.log</string>"
  echo '</dict></plist>'
} > "$FAMILY_PLIST"
launchctl unload "$FAMILY_PLIST" 2>/dev/null || true
launchctl load "$FAMILY_PLIST"
echo "loaded com.housewright.familyevents"

# Rail price check: monthly report-only basket repricing across grocery
# rails. 1st of the month, 7:30am (after the morning brief). launchd
# coalesces a missed fire on wake from sleep; a machine powered OFF for the
# whole 1st skips that month (acceptable for a report-only lane).
RAIL_PLIST="$AGENTS/com.housewright.railcheck.plist"
{
  echo '<?xml version="1.0" encoding="UTF-8"?>'
  echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
  echo '<plist version="1.0"><dict>'
  echo '  <key>Label</key><string>com.housewright.railcheck</string>'
  echo '  <key>ProgramArguments</key><array>'
  echo "    <string>$PY</string>"
  echo "    <string>$ROOT/scripts/rail_check.py</string>"
  echo '  </array>'
  echo "  <key>WorkingDirectory</key><string>$ROOT</string>"
  echo '  <key>EnvironmentVariables</key><dict>'
  echo '    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>'
  echo '    <key>PYTHONUNBUFFERED</key><string>1</string>'
  echo "    <key>TELEGRAM_BOT_TOKEN</key><string>${TELEGRAM_BOT_TOKEN:-}</string>"
  echo '  </dict>'
  echo '  <key>StartCalendarInterval</key><dict>'
  echo '    <key>Day</key><integer>1</integer>'
  echo '    <key>Hour</key><integer>7</integer>'
  echo '    <key>Minute</key><integer>30</integer>'
  echo '  </dict>'
  echo "  <key>StandardOutPath</key><string>$LOGS/com.housewright.railcheck.log</string>"
  echo "  <key>StandardErrorPath</key><string>$LOGS/com.housewright.railcheck.err.log</string>"
  echo '</dict></plist>'
} > "$RAIL_PLIST"
launchctl unload "$RAIL_PLIST" 2>/dev/null || true
launchctl load "$RAIL_PLIST"
echo "loaded com.housewright.railcheck"

echo
echo "Installed. Verify with:  launchctl list | grep com.housewright"
echo "Dashboard:               http://<your-host>:8770/"
echo "NOTE: set TELEGRAM_BOT_TOKEN in your shell BEFORE running this script,"
echo "      or re-run it after setting the token so launchd picks it up."
