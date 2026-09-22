#!/usr/bin/env python3
"""Dedicated native terminal window for Antigravity Remote.

Uses GTK3 and VTE with custom GApplication ID 'com.antigravity.remote'
so it appears as its own dedicated application on the taskbar/dock.
"""
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(DIR, "launcher.py")
ICON_PATH = os.path.join(DIR, "icon.png")
APP_ID = "com.antigravity.remote"

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Vte", "2.91")
    from gi.repository import Gtk, Vte, GLib, Gio, Gdk, GdkPixbuf
except Exception:
    # If GTK or VTE is not available, run launcher directly in current terminal
    os.execv(sys.executable, [sys.executable, LAUNCHER])


class AntigravityRemoteApp(Gtk.Application):
    """GTK Application with dedicated app ID for separate taskbar icon."""

    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )

    def do_activate(self):
        win = Gtk.ApplicationWindow(application=self, title="Antigravity Remote")
        win.set_default_size(860, 560)
        win.set_position(Gtk.WindowPosition.CENTER)

        # Set dedicated window icon from brand icon
        if os.path.exists(ICON_PATH):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(ICON_PATH)
                win.set_icon(pixbuf)
                Gtk.Window.set_default_icon(pixbuf)
            except Exception:
                pass

        # Terminal widget configuration
        terminal = Vte.Terminal()
        terminal.set_scrollback_lines(5000)
        terminal.set_mouse_autohide(True)

        # Soft Studio Palette (#0d0f12 dark background, #f3f4f6 foreground)
        bg = Gdk.RGBA(0.051, 0.059, 0.071, 1.0)
        fg = Gdk.RGBA(0.953, 0.957, 0.965, 1.0)
        terminal.set_color_background(bg)
        terminal.set_color_foreground(fg)

        # Keyboard shortcuts (Ctrl+Shift+C / Ctrl+Shift+V)
        def on_key_press(widget, event):
            state = event.state & Gtk.accelerator_get_default_mod_mask()
            if state == (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
                if event.keyval in (Gdk.KEY_C, Gdk.KEY_c):
                    terminal.copy_clipboard_format(Vte.Format.TEXT)
                    return True
                elif event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
                    terminal.paste_clipboard()
                    return True
            return False

        terminal.connect("key-press-event", on_key_press)

        # Spawn launcher.py asynchronously
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env_list = [f"{k}={v}" for k, v in env.items()]

        def spawn_callback(term, pid, err, user_data=None):
            if err:
                print(f"Spawn error: {err}", file=sys.stderr)

        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            DIR,
            [sys.executable, LAUNCHER] + sys.argv[1:],
            env_list,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            spawn_callback,
            None,
        )

        # Close window when process exits
        terminal.connect("child-exited", lambda term, status: win.close())

        # Scroller container
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(terminal)

        win.add(scroller)
        win.show_all()


def main():
    app = AntigravityRemoteApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
