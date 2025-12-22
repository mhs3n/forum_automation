"""Incremental Datalytics fill:
- Map only first diagnosis/procedure inputs and the add buttons via LLM.
- Fill top fields from payload.
- Loop diagnoses: fill current cell, then click add button to spawn next row.
- Loop procedures: fill code, Tab-to-date, then click add button for next row.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

from agent import ComputerAgent
from computer import Computer
from utils import load_dotenv_files


COORDS_START = r"\[\[\[COORDS_JSON\]\]\]"
COORDS_END = r"\[\[\[END\]\]\]"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Incremental Datalytics filler (add-row per item).")
    p.add_argument(
        "--url",
        default="https://datalytics.drg-pepp.de/drg-pepp-grouper/grouping",
        help="Datalytics URL (default hardcoded).",
    )
    p.add_argument("--data", required=True, help="Path to payload JSON.")
    p.add_argument("--delay", type=float, default=0.5, help="Delay between actions.")
    p.add_argument("--builder-steps", type=int, default=80, help="Max steps for builder stage.")
    p.add_argument("--submit", action="store_true", help="Click submit at end.")
    return p.parse_args()


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_builder_messages(url: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "You are mapping coordinates for Datalytics. Use at most 2 screenshots. "
                "Normalize viewport (zoom ~80%), scroll to top, keep stable. "
                "Open EXACT URL: " + url + " (no localhost). "
                "Map only these elements:\n"
                '- fields: ' + str(list(payload.keys())) + "\n"
                '- diagnosis_add_button (Diagnose hinzufügen)\n'
                '- procedure_add_button (Prozedur hinzufügen)\n'
                "- diagnosis_cells: first diagnosis input only\n"
                "- procedure_rows: first procedure code input + its date input (same row)\n"
                "- submit button (optional)\n"
                "Do NOT fill data. Do NOT add extra rows. Just map coords. "
                "Output ONLY JSON wrapped in [[[COORDS_JSON]]] ... [[[END]]]:\n"
                "{\n"
                '  "fields": [ {"key": "<json_key>", "x": <int>, "y": <int>}, ... ],\n'
                '  "diagnosis_add_button": {"x": <int>, "y": <int>},\n'
                '  "procedure_add_button": {"x": <int>, "y": <int>},\n'
                '  "diagnosis_cells": [ {"x": <int>, "y": <int>} ],\n'
                '  "procedure_rows": [ {"code": {"x": <int>, "y": <int>}, "date": {"x": <int>, "y": <int>}} ],\n'
                '  "submit": {"x": <int>, "y": <int>}  // optional\n'
                "}\n"
                "Respond only with the JSON in markers."
            ),
        }
    ]


def extract_coords_json(text: str) -> Optional[Dict[str, Any]]:
    m = re.search(rf"{COORDS_START}(.*?){COORDS_END}", text, re.DOTALL)
    if not m:
        return None
    block = m.group(1).strip()
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        return None


async def run_agent_for_coords(agent: ComputerAgent, messages: List[Dict[str, Any]], max_steps: int) -> Dict[str, Any]:
    steps = 0
    coords: Optional[Dict[str, Any]] = None
    async for result in agent.run(messages, stream=False):
        steps += 1
        for item in result.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    txt = part.get("text")
                    if txt:
                        print(f"[builder] {txt}")
                        maybe = extract_coords_json(txt)
                        if maybe:
                            coords = maybe
        if coords:
            break
        if steps >= max_steps:
            break
    if not coords:
        raise RuntimeError("Failed to extract coords JSON from builder output.")
    return coords


async def apply_fill(computer: Computer, payload: Dict[str, Any], coords: Dict[str, Any], delay: float, submit: bool) -> None:
    iface = computer._interface  # type: ignore[attr-defined]
    if iface is None:
        raise RuntimeError("Computer interface not initialized.")

    def fmt_value(v: Any) -> str:
        if isinstance(v, list):
            return ", ".join(str(item) for item in v)
        return str(v)

    async def clear_field() -> None:
        await iface.press("ctrl+a")
        await asyncio.sleep(delay * 0.2)
        for _ in range(10):
            await iface.press("backspace")
            await asyncio.sleep(delay * 0.05)
        for _ in range(10):
            await iface.press("delete")
            await asyncio.sleep(delay * 0.05)

    async def click_and_type(x: int, y: int, text: str) -> None:
        await iface.left_click(int(x), int(y))
        await asyncio.sleep(delay)
        await clear_field()
        if text:
            await iface.type_text(text)
        await asyncio.sleep(delay)

    # Top fields
    for field in coords.get("fields", []):
        key = field.get("key")
        x, y = field.get("x"), field.get("y")
        if key is None or x is None or y is None:
            continue
        val = fmt_value(payload.get(key, ""))
        print(f"[fill] {key} -> ({x},{y}) '{val}'")
        await click_and_type(int(x), int(y), val)

    diag_cell = coords.get("diagnosis_cells", [{}])[0]
    diag_add = coords.get("diagnosis_add_button")
    procs_rows = coords.get("procedure_rows", [{}])
    proc_add = coords.get("procedure_add_button")
    if not diag_cell:
        print("[fill] missing diagnosis cell coord; skipping diagnoses.")
    if not procs_rows or not procs_rows[0].get("code"):
        print("[fill] missing procedure row coord; skipping procedures.")

    # Diagnoses loop
    diags = payload.get("diagnoses", [])
    for i, d in enumerate(diags):
        if not diag_cell:
            break
        print(f"[fill] diagnosis[{i}] -> ({diag_cell['x']},{diag_cell['y']}) '{d}'")
        await click_and_type(int(diag_cell["x"]), int(diag_cell["y"]), fmt_value(d))
        if diag_add and i < len(diags) - 1:
            await iface.left_click(int(diag_add["x"]), int(diag_add["y"]))
            await asyncio.sleep(delay)

    # Procedures loop
    procs = payload.get("procedures", [])
    dates = payload.get("procedure_dates", [])
    base_row = procs_rows[0] if procs_rows else {}
    code_cell = base_row.get("code")
    date_cell = base_row.get("date")
    for i, p in enumerate(procs):
        if not code_cell or not date_cell:
            break
        print(f"[fill] procedure[{i}] code -> ({code_cell['x']},{code_cell['y']}) '{p}'")
        await click_and_type(int(code_cell["x"]), int(code_cell["y"]), fmt_value(p))
        d_txt = fmt_value(dates[i]) if i < len(dates) else ""
        print(f"[fill] procedure[{i}] date -> ({date_cell['x']},{date_cell['y']}) '{d_txt}'")
        await click_and_type(int(date_cell["x"]), int(date_cell["y"]), d_txt)
        if proc_add and i < len(procs) - 1:
            await iface.left_click(int(proc_add["x"]), int(proc_add["y"]))
            await asyncio.sleep(delay)

    if submit and coords.get("submit"):
        sx, sy = coords["submit"].get("x"), coords["submit"].get("y")
        if sx is not None and sy is not None:
            print(f"[fill] submit at ({sx},{sy})")
            await iface.left_click(int(sx), int(sy))
            await asyncio.sleep(delay)

    print("[fill] done.")


async def main_async(args: argparse.Namespace) -> None:
    load_dotenv_files()
    payload = load_json(args.data)
    host = os.getenv("CUA_HOST", "127.0.0.1")
    port = int(os.getenv("CUA_PORT", "8000"))
    model = os.getenv("CUA_MODEL", "anthropic/claude-opus-4-5-20251101")

    async with Computer(os_type="linux", use_host_computer_server=True, host=host, port=port) as computer:
        builder = ComputerAgent(
            model=model,
            tools=[computer],
            only_n_most_recent_images=2,
            max_trajectory_budget=5.0,
        )
        coords = await run_agent_for_coords(
            builder,
            build_builder_messages(args.url, payload),
            max_steps=args.builder_steps,
        )
        # Save coords for reuse
        os.makedirs("data", exist_ok=True)
        with open("data/datalytics_coords_incremental.json", "w", encoding="utf-8") as f:
            json.dump(coords, f, indent=2)
        print("[runner] saved coords to data/datalytics_coords_incremental.json")

        await apply_fill(computer, payload, coords, args.delay, args.submit)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
