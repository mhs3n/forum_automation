#!/usr/bin/env python3
"""
Record mouse clicks and keypresses relative to a specific X11 window title.
Outputs a JSON with actions and timestamps. Intended to run on the host while
you drive a VNC/RDP client window; guest remains untouched.

Example:
  pip install python-xlib
  python scripts/x11_window_recorder.py --window-title "Remmina" --out trajectories/live_macro.json --duration 60
Press Ctrl+C to stop early.
"""

import argparse
import json
import re
import signal
import sys
import time
from typing import Any, Dict, List, Optional

from Xlib import X, XK, display, error


def keysym_to_str(keysym: int) -> str:
    name = XK.keysym_to_string(keysym)
    return name if name else f"keysym_{keysym}"


def find_window(d, title_re: re.Pattern) -> Optional[Any]:
    root = d.screen().root
    stack = [root]
    while stack:
        win = stack.pop()
        try:
            name = win.get_wm_name()
        except error.XError:
            name = None
        if name and title_re.search(str(name)):
            return win
        try:
            children = win.query_tree().children
        except error.XError:
            children = []
        stack.extend(children)
    return None


def window_abs_position(win):
    geo = win.get_geometry()
    coords = win.translate_coords(win.display.screen().root, 0, 0)
    return coords.dest_x, coords.dest_y, geo.width, geo.height


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record clicks/keys relative to a window title.")
    p.add_argument("--window-title", required=True, help="Regex for window title (e.g., 'Remmina').")
    p.add_argument("--out", default="trajectories/live_macro.json", help="Output JSON path.")
    p.add_argument("--display", default=":0", help="X11 display (default :0).")
    p.add_argument("--duration", type=float, default=0.0, help="Auto-stop after N seconds (0 = no limit).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    d = display.Display(args.display)
    title_re = re.compile(args.window_title)
    target = find_window(d, title_re)
    if not target:
        print(f"[error] window not found matching: {args.window_title}")
        return 1
    abs_x, abs_y, w, h = window_abs_position(target)
    print(f"[rec] found window at ({abs_x},{abs_y}) size {w}x{h}")

    root = d.screen().root
    root.change_attributes(event_mask=X.KeyPressMask | X.ButtonPressMask)

    start = time.monotonic()
    max_until = start + args.duration if args.duration > 0 else None
    stopped = False
    actions: List[Dict[str, Any]] = []

    def handle_sigint(signum, frame):  # type: ignore[override]
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, handle_sigint)
    print("Recording... Ctrl+C to stop.")

    while not stopped:
        if max_until and time.monotonic() >= max_until:
            stopped = True
            break
        ev = d.next_event()
        ts_ms = int((time.monotonic() - start) * 1000)
        # Refresh window position in case it moved
        abs_x, abs_y, w, h = window_abs_position(target)
        if ev.type == X.ButtonPress:
            rx, ry = ev.root_x, ev.root_y
            if not (abs_x <= rx < abs_x + w and abs_y <= ry < abs_y + h):
                continue
            rel_x, rel_y = rx - abs_x, ry - abs_y
            actions.append(
                {"type": "click", "button": ev.detail, "x": rel_x, "y": rel_y, "ts_ms": ts_ms}
            )
            print(f"[rec] {ts_ms:6d}ms click ({rel_x},{rel_y}) btn={ev.detail}")
        elif ev.type == X.KeyPress:
            # Only record keys if the target window (or its children) has focus
            try:
                focus = d.get_input_focus().focus
                in_chain = False
                cur = focus
                while cur:
                    if cur.id == target.id:
                        in_chain = True
                        break
                    parent = None
                    try:
                        parent = cur.query_tree().parent
                    except error.XError:
                        parent = None
                    cur = parent
                if not in_chain:
                    continue
            except error.XError:
                continue
            key_name = keysym_to_str(d.keycode_to_keysym(ev.detail, 0))
            actions.append({"type": "keypress", "keys": [key_name], "ts_ms": ts_ms})
            print(f"[rec] {ts_ms:6d}ms keypress {key_name}")

    payload = {"actions": actions}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"[rec] saved {len(actions)} actions to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
