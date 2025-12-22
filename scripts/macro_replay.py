"""Replay a recorded macro plan (actions) with coords and payload via CUA."""

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
    p = argparse.ArgumentParser(description="Replay macro actions via CUA.")
    p.add_argument("--data", required=True, help="Path to payload JSON.")
    p.add_argument("--coords", required=True, help="Path to coords JSON.")
    p.add_argument("--actions", required=True, help="Path to macro actions JSON.")
    p.add_argument("--delay", type=float, default=0.35, help="Delay between actions (seconds).")
    p.add_argument("--tab-walk", action="store_true", default=True, help="Tab-walk scalar fields first (optional).")
    p.add_argument("--no-tab-walk", dest="tab_walk", action="store_false", help="Disable scalar tab-walk.")
    return p.parse_args()


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def replay(computer: Computer, data: Dict[str, Any], coords: Dict[str, Any], actions: List[Dict[str, Any]], delay: float, tab_walk: bool) -> None:
    iface = computer._interface  # type: ignore[attr-defined]
    if iface is None:
        raise RuntimeError("Computer interface not initialized.")

    fields = coords.get("fields", [])
    diag_cells = coords.get("diagnosis_cells", [])
    proc_rows = coords.get("procedure_rows", [])

    def get_coord(key: str) -> Dict[str, Any]:
        for f in fields:
            if f.get("key") == key:
                return f
        raise KeyError(f"Missing coord for {key}")

    async def clear_field() -> None:
        await iface.press("ctrl+a")
        await asyncio.sleep(delay * 0.2)
        for _ in range(10):
            await iface.press("backspace")
            await asyncio.sleep(delay * 0.05)
        for _ in range(10):
            await iface.press("delete")
            await asyncio.sleep(delay * 0.05)

    async def click_and_type_coord(coord: Dict[str, Any], text: str) -> None:
        await iface.left_click(int(coord["x"]), int(coord["y"]))
        await asyncio.sleep(delay)
        await clear_field()
        if text:
            await iface.type_text(text)
        await asyncio.sleep(delay)

    async def type_in_place(text: str) -> None:
        await clear_field()
        if text:
            await iface.type_text(text)
        await asyncio.sleep(delay)

    # Optional scalar tab-walk seed if requested and actions include scalar fields
    if tab_walk:
        try:
            gender_coord = get_coord("gender")
            await iface.left_click(int(gender_coord["x"]), int(gender_coord["y"]))
            await asyncio.sleep(delay)
        except Exception:
            pass

    for act in actions:
        op = act.get("op")
        if op == "field":
            key = act.get("key")
            coord = get_coord(key)
            val = data.get(key, "")
            print(f"[replay] field {key} -> ({coord['x']},{coord['y']}) '{val}'")
            await click_and_type_coord(coord, str(val))
            if tab_walk:
                await iface.press("tab")
                await asyncio.sleep(delay)
        elif op == "diagnosis":
            idx = act.get("index", 0)
            if idx < len(diag_cells):
                coord = diag_cells[idx]
                val = data.get("diagnoses", [])
                val = val[idx] if idx < len(val) else ""
                print(f"[replay] diagnosis[{idx}] -> ({coord['x']},{coord['y']}) '{val}'")
                await click_and_type_coord(coord, str(val))
        elif op == "procedure_code":
            idx = act.get("index", 0)
            if idx < len(proc_rows):
                coord = proc_rows[idx].get("code")
                if coord:
                    val = data.get("procedures", [])
                    val = val[idx] if idx < len(val) else ""
                    print(f"[replay] procedure[{idx}] code -> ({coord['x']},{coord['y']}) '{val}'")
                    await click_and_type_coord(coord, str(val))
        elif op == "procedure_date":
            idx = act.get("index", 0)
            if idx < len(proc_rows):
                coord = proc_rows[idx].get("date")
                if coord:
                    val = data.get("procedure_dates", [])
                    val = val[idx] if idx < len(val) else ""
                    print(f"[replay] procedure[{idx}] date (tab) -> '{val}'")
                    await iface.press("tab")
                    await asyncio.sleep(delay)
                    await type_in_place(str(val))
        else:
            print(f"[replay] skipping unknown op {op}")

    print("[replay] done (submit not triggered here).")


async def main_async(args: argparse.Namespace) -> None:
    load_dotenv_files()
    data = load_json(args.data)
    coords = load_json(args.coords)
    actions = load_json(args.actions).get("actions", [])

    async with Computer(os_type="linux", use_host_computer_server=True, host=os.getenv("CUA_HOST", "127.0.0.1"), port=int(os.getenv("CUA_PORT", "8000"))) as computer:
        await replay(computer, data, coords, actions, args.delay, args.tab_walk)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
