"""Manual recorder: capture your own clicks/keypress/type into a JSON macro via CUA.

Usage:
  python scripts/manual_recorder.py --out trajectories/manual_macro.json --delay 0.35

- Uses the existing CUA host computer interface.
- Records: click, type_text, press (keypress), wait (if you add pauses).
- Does NOT enforce masking; you can choose to mask later if needed.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List

from computer import Computer
from utils import load_dotenv_files


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Manual macro recorder via CUA host interface.")
    p.add_argument("--out", default="trajectories/manual_macro.json", help="Where to save recorded macro JSON.")
    p.add_argument("--delay", type=float, default=0.35, help="Delay between actions when replayed (stored in metadata).")
    return p.parse_args()


async def record_actions(computer: Computer) -> List[Dict[str, Any]]:
    iface = computer._interface  # type: ignore[attr-defined]
    if iface is None:
        raise RuntimeError("Computer interface not initialized.")

    actions: List[Dict[str, Any]] = []

    print("Manual recorder started.")
    print("Commands:")
    print("  click x y           (left click)")
    print("  type your text      (type literal text)")
    print("  press KEY           (keypress, e.g., press Enter or press ctrl+l)")
    print("  wait seconds        (sleep)")
    print("  done                (finish and save)")

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line.lower() == "done":
            break
        parts = line.split()
        cmd = parts[0].lower()
        if cmd == "click" and len(parts) == 3:
            try:
                x = int(parts[1]); y = int(parts[2])
                await iface.left_click(x, y)
                actions.append({"type": "click", "x": x, "y": y})
            except ValueError:
                print("Invalid coords.")
        elif cmd == "type" and len(parts) >= 2:
            text = line[len("type "):]
            await iface.type_text(text)
            actions.append({"type": "type", "text": text})
        elif cmd == "press" and len(parts) >= 2:
            keys = parts[1:]
            for k in keys:
                await iface.press(k)
            actions.append({"type": "keypress", "keys": keys})
        elif cmd == "wait" and len(parts) == 2:
            try:
                secs = float(parts[1])
                await asyncio.sleep(secs)
                actions.append({"type": "wait", "seconds": secs})
            except ValueError:
                print("Invalid seconds.")
        else:
            print("Unknown command.")

    return actions


async def main_async(args: argparse.Namespace) -> None:
    load_dotenv_files()
    host = os.getenv("CUA_HOST", "127.0.0.1")
    port = int(os.getenv("CUA_PORT", "8000"))
    async with Computer(os_type="linux", use_host_computer_server=True, host=host, port=port) as computer:
        actions = await record_actions(computer)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"actions": actions, "delay": args.delay}, f, indent=2)
    print(f"Saved {len(actions)} actions to {args.out}")


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
