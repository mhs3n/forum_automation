"""
X11 event recorder to capture live mouse clicks and keypresses into a JSON macro.

Intended to run inside the CUA desktop container (Xfce/VNC). It listens to the
root window and records:
  - ButtonPress events as {"type": "click", "x": ..., "y": ...}
  - KeyPress events as {"type": "keypress", "keys": ["..."]}

Exit with Ctrl+C to write the JSON file.

Example (inside container):
  pip install python-xlib
  DISPLAY=:1 python /tmp/x11_event_recorder.py --out /tmp/live_macro.json
"""

import argparse
import json
import signal
import sys
import time
from typing import Any, Dict, List

from Xlib import X, XK, display, error, protocol
try:
    from Xlib.ext import record
    HAS_RECORD = True
except Exception:
    HAS_RECORD = False


def keysym_to_str(keysym: int) -> str:
    """Convert a keysym integer to a human-readable string."""
    name = XK.keysym_to_string(keysym)
    return name if name else f"keysym_{keysym}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record live X11 clicks/keypresses to JSON.")
    p.add_argument("--out", default="trajectories/live_macro.json", help="Output JSON path.")
    p.add_argument("--display", default=":1", help="X11 display (default :1 for the VNC session).")
    p.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="Stored replay delay metadata (not used during recording).",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Optional max duration in seconds before autosave/exit (0 = no limit).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    actions: List[Dict[str, Any]] = []

    d = display.Display(args.display)
    use_record = HAS_RECORD and d.has_extension("RECORD")
    if not use_record:
        print("[warn] X RECORD extension not available; will try root event mask.")

    start = time.monotonic()
    stopped = False

    def handle_sigint(signum, frame):  # type: ignore[override]
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, handle_sigint)
    print("Recording... press Ctrl+C to stop (or kill the process).")

    if use_record:
        try:
            ctx = d.record_create_context(
                0,
                [record.AllClients],
                [
                    {
                        "core_requests": (0, 0),
                        "core_replies": (0, 0),
                        "ext_requests": (0, 0, 0, 0),
                        "ext_replies": (0, 0, 0, 0),
                        "delivered_events": (X.KeyPress, X.ButtonPress),
                        "device_events": (X.KeyPress, X.ButtonPress),
                        "errors": (0, 0),
                        "client_started": False,
                        "client_died": False,
                    }
                ],
            )
        except Exception as e:
            print(f"[warn] Failed to init RECORD ({e}); falling back to root mask.")
            use_record = False

    if use_record:
        def record_cb(reply):
            nonlocal stopped
            if stopped:
                return
            if reply.category != record.FromServer:
                return
            data = reply.data
            while len(data):
                event, data = protocol.rq.EventField(None).parse_binary_value(
                    data, d.display, None, None
                )
                ts_ms = int((time.monotonic() - start) * 1000)
                if event.type == X.KeyPress:
                    key_name = keysym_to_str(d.keycode_to_keysym(event.detail, 0))
                    actions.append({"type": "keypress", "keys": [key_name], "ts_ms": ts_ms})
                    print(f"[rec] {ts_ms:6d}ms keypress {key_name}")
                elif event.type == X.ButtonPress:
                    actions.append(
                        {
                            "type": "click",
                            "button": event.detail,
                            "x": event.root_x,
                            "y": event.root_y,
                            "ts_ms": ts_ms,
                        }
                    )
                    print(
                        f"[rec] {ts_ms:6d}ms click ({event.root_x},{event.root_y}) btn={event.detail}"
                    )

        # RECORD enable blocks; run it in a try/finally so Ctrl+C exits.
        try:
            d.record_enable_context(ctx, record_cb)
        finally:
            d.record_free_context(ctx)
    else:
        root = d.screen().root
        try:
            root.change_attributes(event_mask=X.KeyPressMask | X.ButtonPressMask)
        except error.BadAccess:
            print("[error] Cannot grab root events (BadAccess). Are you on the right DISPLAY?")
            return 1

    max_until = start + args.duration if args.duration > 0 else None
    while not stopped:
        if max_until and time.monotonic() >= max_until:
            stopped = True
            break
        event = d.next_event()
        ts_ms = int((time.monotonic() - start) * 1000)
        if event.type == X.KeyPress:
            keysym = d.keycode_to_keysym(event.detail, 0)
            key_name = keysym_to_str(keysym)
                actions.append({"type": "keypress", "keys": [key_name], "ts_ms": ts_ms})
                print(f"[rec] {ts_ms:6d}ms keypress {key_name}")
            elif event.type == X.ButtonPress:
                actions.append(
                    {
                        "type": "click",
                        "button": event.detail,
                        "x": event.root_x,
                        "y": event.root_y,
                        "ts_ms": ts_ms,
                    }
                )
                print(f"[rec] {ts_ms:6d}ms click ({event.root_x},{event.root_y}) btn={event.detail}")

    # Save on exit
    payload = {"actions": actions, "delay": args.delay}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved {len(actions)} actions to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
