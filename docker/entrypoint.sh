#!/usr/bin/env bash
set -u

export DISPLAY="${DISPLAY:-:99}"
W="${SCREEN_W:-1280}"
H="${SCREEN_H:-800}"
AUTHFILE=/tmp/xvfb.auth

rm -f "/tmp/.X${DISPLAY#:}-lock" "/tmp/.X11-unix/X${DISPLAY#:}"

# fresh sandbox on every start: no leftovers from earlier runs or earlier customers
rm -rf /root/projects/* /root/Documents/* /root/Downloads/* 2>/dev/null || true
rm -rf /root/.cache /root/.npm /root/.bun /root/.mozilla 2>/dev/null || true
rm -f /root/.bash_history /tmp/tivm_*.png /tmp/ff-warm*.png 2>/dev/null || true

touch "$AUTHFILE"
export XAUTHORITY="$AUTHFILE"

Xvfb "$DISPLAY" -screen 0 "${W}x${H}x24" -auth "$AUTHFILE" -ac -nolisten tcp &
sleep 1
xset s off -dpms 2>/dev/null || true

dbus-daemon --system --fork 2>/dev/null || true
eval "$(dbus-launch --sh-syntax 2>/dev/null)" || true

export NO_AT_BRIDGE=0
export GIT_TERMINAL_PROMPT=0
gsettings set org.gnome.desktop.interface toolkit-accessibility true 2>/dev/null || true

startxfce4 >/tmp/xfce.log 2>&1 &
sleep 2

(
  while true; do
    x11vnc -display "$DISPLAY" -auth "$AUTHFILE" -forever -shared -nopw -quiet -rfbport 5900 -o /tmp/x11vnc.log
    echo "x11vnc exited ($?), restarting in 1s" >>/tmp/x11vnc.log
    sleep 1
  done
) &
websockify --web=/usr/share/novnc 6080 localhost:5900 >/tmp/websockify.log 2>&1 &

cd /app
exec uvicorn agent.server:app --host 0.0.0.0 --port 6081 --log-level warning
