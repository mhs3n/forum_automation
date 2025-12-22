"""Fill a form via fixed screen coordinates using the CUA computer interface.

This runs directly through the existing CUA host connection (no host xdotool).
It clicks coordinates and types values from a JSON payload.

Usage:
  python scripts/coord_macro_runner.py --data data/payload.json --coords data/coords.json

coords.json format (ordered):
{
  "fields": [
    {"key": "gender", "x": 321, "y": 155},
    {"key": "age", "x": 254, "y": 482},
    ...
  ],
  "submit": {"x": 500, "y": 700}   # optional
}

Notes:
- Keep display resolution, browser zoom, and window position constant; coordinate
  macros are brittle to layout changes.
- Values in the data JSON can be strings, numbers, or lists; lists are joined
  with ", ".
- This script does NOT open the page; run it after the form is visible.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from computer import Computer
from utils import load_dotenv_files


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Coordinate-based form filler via CUA interface.")
    p.add_argument("--data", required=True, help="Path to JSON payload with values.")
    p.add_argument("--coords", required=True, help="Path to JSON mapping keys to coordinates.")
    p.add_argument("--delay", type=float, default=0.25, help="Delay between actions (seconds).")
    p.add_argument("--host", default=None, help="Override CUA_HOST (default from env).")
    p.add_argument("--port", type=int, default=None, help="Override CUA_PORT (default from env).")
    return p.parse_args()


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_value(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(item) for item in v)
    return str(v)


async def run_macro(
    computer: Computer,
    data: Dict[str, Any],
    coords: Dict[str, Any],
    delay: float,
) -> None:
    iface = computer._interface  # type: ignore[attr-defined]
    if iface is None:
        raise RuntimeError("Computer interface not initialized.")

    fields: List[Dict[str, Any]] = coords.get("fields", [])
    submit: Optional[Dict[str, Any]] = coords.get("submit")

    async def clear_field() -> None:
        await iface.press("ctrl+a")
        await asyncio.sleep(delay * 0.2)
        for _ in range(10):
            await iface.press("backspace")
            await asyncio.sleep(delay * 0.05)
        for _ in range(10):
            await iface.press("delete")
            await asyncio.sleep(delay * 0.05)

    for field in fields:
        key = field.get("key")
        x = field.get("x")
        y = field.get("y")
        if key is None or x is None or y is None:
            print(f"[macro] skipping invalid field entry: {field}")
            continue
        val = data.get(key, "")
        text = fmt_value(val)
        print(f"[macro] {key} -> click ({x},{y}), type '{text}'")
        await iface.left_click(int(x), int(y))
        await asyncio.sleep(delay)
        await clear_field()
        if text:
            await iface.type_text(text)
        await asyncio.sleep(delay)

    if submit and "x" in submit and "y" in submit:
        sx = int(submit["x"])
        sy = int(submit["y"])
        print(f"[macro] submit click at ({sx},{sy})")
        await iface.left_click(sx, sy)
        await asyncio.sleep(delay)

    print("[macro] done.")


def main() -> int:
    args = parse_args()
    load_dotenv_files()

    host = args.host or os.getenv("CUA_HOST", "127.0.0.1")
    port = args.port or int(os.getenv("CUA_PORT", "8000"))

    logging.basicConfig(level=logging.INFO)

    data = load_json(args.data)
    coords = load_json(args.coords)

    async def flow():
        async with Computer(os_type="linux", use_host_computer_server=True, host=host, port=port) as computer:
            await run_macro(computer, data, coords, args.delay)

    asyncio.run(flow())
    return 0


if __name__ == "__main__":
    sys.exit(main())
