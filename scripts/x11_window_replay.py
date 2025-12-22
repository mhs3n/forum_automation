#!/usr/bin/env python3
"""
Replay a window-relative macro recorded with x11_window_recorder.py.
Finds the target window by title regex, focuses it, and replays clicks/keys
with original timing (ts_ms). Requires XTEST extension.

Example:
  python scripts/x11_window_replay.py --window-title "Remmina" --actions trajectories/live_macro.json
"""

import argparse
import json
import re
import sys
import time
from typing import Any, Dict, List, Optional

from Xlib import X, XK, display, error
from Xlib.ext import xtest


def keysym_from_name(d, name: str) -> Optional[int]:
    ks = XK.string_to_keysym(name)
    if ks == 0:
        return None
    return ks


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


def focus_window_with_xtest(d, win):
    # Best-effort focus: warp pointer inside and click to focus
    abs_x, abs_y, w, h = window_abs_position(win)
    cx, cy = abs_x + w // 2, abs_y + h // 2
    xtest.fake_input(d, X.MotionNotify, x=cx, y=cy)
    xtest.fake_input(d, X.ButtonPress, 1)
    xtest.fake_input(d, X.ButtonRelease, 1)
    d.sync()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Replay window-relative macro.")
    p.add_argument("--window-title", required=True, help="Regex for window title (e.g., 'Remmina').")
    p.add_argument("--actions", required=True, help="Path to JSON with actions.")
    p.add_argument("--display", default=":0", help="X11 display (default :0).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    d = display.Display(args.display)
    if not d.has_extension("XTEST"):
        print("[error] XTEST extension not available on this X server.")
        return 1

    title_re = re.compile(args.window_title)
    win = find_window(d, title_re)
    if not win:
        print(f"[error] window not found matching: {args.window_title}")
        return 1
    abs_x, abs_y, w, h = window_abs_position(win)
    print(f"[replay] target window at ({abs_x},{abs_y}) size {w}x{h}")

    with open(args.actions, "r", encoding="utf-8") as f:
        payload = json.load(f)
    actions: List[Dict[str, Any]] = payload.get("actions", [])
    if not actions:
        print("[error] no actions found.")
        return 1

    focus_window_with_xtest(d, win)
    start = time.monotonic()
    first_ts = actions[0].get("ts_ms", 0)

    for act in actions:
        ts = act.get("ts_ms", 0) - first_ts
        wait = ts / 1000.0 - (time.monotonic() - start)
        if wait > 0:
            time.sleep(wait)
        typ = act.get("type")
        if typ == "click":
            rx = act["x"]
            ry = act["y"]
            bx = abs_x + rx
            by = abs_y + ry
            btn = act.get("button", 1)
            xtest.fake_input(d, X.MotionNotify, x=bx, y=by)
            xtest.fake_input(d, X.ButtonPress, btn)
            xtest.fake_input(d, X.ButtonRelease, btn)
            d.sync()
        elif typ == "keypress":
            keys = act.get("keys", [])
            for name in keys:
                ks = keysym_from_name(d, name)
                if ks is None:
                    continue
                kc = d.keysym_to_keycode(ks)
                xtest.fake_input(d, X.KeyPress, kc)
                xtest.fake_input(d, X.KeyRelease, kc)
                d.sync()

    print(f"[replay] done ({len(actions)} actions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
