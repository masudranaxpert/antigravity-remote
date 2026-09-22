#!/usr/bin/env bash
# Dedicated launcher for Antigravity Remote
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# If running directly inside an interactive shell/terminal, run text launcher
if [ -t 0 ] && [ "$1" == "--cli" ]; then
    shift
    exec python3 "$DIR/launcher.py" "$@"
fi

# If graphical session is present, launch dedicated GTK window with unique app ID
if [ -n "$WAYLAND_DISPLAY" ] || [ -n "$DISPLAY" ]; then
    exec python3 "$DIR/terminal_gui.py" "$@"
fi

# Fallback to direct Python runner
exec python3 "$DIR/launcher.py" "$@"
