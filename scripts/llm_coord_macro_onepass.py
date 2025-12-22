"""One-pass flow: LLM maps fields -> coords (<=3 screenshots), macro fills, optional verify/fix.

Steps:
 1) Agent opens URL, uses <=3 screenshots, outputs coords JSON (no filling).
    JSON is wrapped in markers [[[COORDS_JSON]]] ... [[[END]]].
 2) Script runs a deterministic coordinate macro (click/type) via the CUA interface
    using the provided payload JSON and the extracted coords.
 3) Optional: Agent verifies submission (read-only). If mismatches, it returns a FIX_ACTIONS
    JSON that this script applies, then re-verifies once.

Usage:
  python scripts/llm_coord_macro_onepass.py \
    --url https://www.drg-research-group.de/index.php?option=com_webgrouper&view=webgrouper&Itemid=112 \
    --data data/payload.json \
    --coords-out data/coords_generated.json
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
    p = argparse.ArgumentParser(description="One-pass: LLM coords -> macro fill -> optional LLM verify.")
    p.add_argument("--url", required=True, help="Target URL.")
    p.add_argument("--data", required=True, help="Path to JSON payload with form data.")
    p.add_argument("--coords-out", default="data/coords_generated.json", help="Where to save coords JSON.")
    p.add_argument("--builder-steps", type=int, default=60, help="Max steps for builder stage (default 60).")
    p.add_argument("--verify-steps", type=int, default=10, help="Max steps for verify stage (default 10; set 0 to skip).")
    p.add_argument("--delay", type=float, default=0.35, help="Delay between macro actions (seconds).")
    p.add_argument(
        "--tab-walk",
        action="store_true",
        default=True,
        help="Use tab-walk starting from gender for scalar fields (default: on).",
    )
    p.add_argument(
        "--no-tab-walk",
        action="store_false",
        dest="tab_walk",
        help="Disable tab-walk; fill scalar fields by per-field coordinates.",
    )
    return p.parse_args()


def load_payload(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_value(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(item) for item in v)
    return str(v)


def build_builder_messages(url: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "You are building a coordinate-based macro plan. Rules:\n"
                "- Use at most 2 screenshots total.\n"
                "- DO NOT fill the form now. Only inspect and map keys to coordinates.\n"
                "- Normalize the viewport first: set browser zoom to 80% (Ctrl+- until ~80%), then scroll to the top, then scroll once to a position where ALL target fields are simultaneously visible. Do not change zoom or scroll after that.\n"
                "- Open the page, locate fields for these JSON keys, and output coords JSON.\n"
                "- When done, reply ONLY with the JSON wrapped in markers [[[COORDS_JSON]]] ... [[[END]]]. No extra text before/after.\n"
                "JSON format:\n"
                "{\n"
                '  "fields": [ {"key": "<json_key>", "x": <int>, "y": <int>}, ... ],\n'
                '  "diagnosis_cells": [ {"x": <int>, "y": <int>}, ... ],\n'
                '  "procedure_rows": [ {"code": {"x": <int>, "y": <int>}, "date": {"x": <int>, "y": <int>}}, ... ],\n'
                '  "submit": {"x": <int>, "y": <int>}  // optional\n'
                "}\n"
                "- The diagnosis_cells array must have one entry per diagnosis value in order.\n"
                "- The procedure_rows array must have one entry per procedure; each has code and its date cell.\n"
                "- Final response example:\n"
                "[[[COORDS_JSON]]]\n"
                "{ \"fields\": [], \"diagnosis_cells\": [], \"procedure_rows\": [], \"submit\": {\"x\":1,\"y\":1} }\n"
                "[[[END]]]\n"
                f"Use ONLY these keys: {list(payload.keys())}\n"
                f"Target URL: {url}\n"
                "Do not invent values; just provide coords."
            ),
        }
    ]


FIX_START = r"\[\[\[FIX_ACTIONS\]\]\]"
FIX_END = r"\[\[\[END\]\]\]"


def build_verify_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "Verify the form submission/result reflects the provided data. "
                "Use minimal screenshots (<=2). Do NOT edit anything. "
                "If everything matches, say 'OK'. "
                "Treat 'G-DRG 2025' as matching drg_version '76' (do NOT flag as a mismatch). "
                "Treat admission_reason 'N' as matching 'Notfall'. "
                "Accept 4384,9 and 4384.9 as equivalent base_rate formats. "
                "If mismatches exist, output a JSON of fixes wrapped in [[[FIX_ACTIONS]]] ... [[[END]]], with format:\n"
                "[[[FIX_ACTIONS]]]\n"
                "{\n"
                '  "fields": [ {"key": "age", "value": "64"}, ... ],\n'
                '  "diagnoses": [ {"index": 0, "value": "I80.28"}, ... ],\n'
                '  "procedures": [ {"index": 0, "code": "3-035:l", "date": "22.11.2025"}, ... ]\n'
                "}\n"
                f"Data: {json.dumps(payload, indent=2)}"
            ),
        }
    ]


def extract_coords_json(text: str) -> Optional[Dict[str, Any]]:
    pattern = rf"{COORDS_START}(.*?){COORDS_END}"
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        return None
    block = m.group(1).strip()
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        return None


def extract_fix_actions(text: str) -> Optional[Dict[str, Any]]:
    pattern = rf"{FIX_START}(.*?){FIX_END}"
    m = re.search(pattern, text, re.DOTALL)
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
                        print(f"[agent-builder] {txt}")
                        maybe = extract_coords_json(txt)
                        if maybe:
                            coords = maybe
        if coords:
            print("[builder] extracted coords JSON.")
            break
        if steps >= max_steps:
            print(f"[builder] reached {max_steps} steps without coords; stopping.")
            break
    if not coords:
        raise RuntimeError("Failed to extract coords JSON from agent output.")
    return coords


async def run_macro(computer: Computer, data: Dict[str, Any], coords: Dict[str, Any], delay: float, use_tab_walk: bool, recorder: Optional[List[Dict[str, Any]]] = None) -> None:
    iface = computer._interface  # type: ignore[attr-defined]
    if iface is None:
        raise RuntimeError("Computer interface not initialized.")
    await apply_fill(computer, data, coords, delay, use_tab_walk, recorder)


async def apply_fill(computer: Computer, data: Dict[str, Any], coords: Dict[str, Any], delay: float, use_tab_walk: bool, recorder: Optional[List[Dict[str, Any]]] = None) -> None:
    iface = computer._interface  # type: ignore[attr-defined]
    if iface is None:
        raise RuntimeError("Computer interface not initialized.")

    fields = coords.get("fields", [])
    diag_cells = coords.get("diagnosis_cells", [])
    proc_rows = coords.get("procedure_rows", [])
    submit = coords.get("submit")

    def get_coord(key: str) -> Optional[Dict[str, Any]]:
        for f in fields:
            if f.get("key") == key:
                return f
        return None

    async def clear_field() -> None:
        await iface.press("ctrl+a")
        await asyncio.sleep(delay * 0.2)
        for _ in range(10):
            await iface.press("backspace")
            await asyncio.sleep(delay * 0.05)
        for _ in range(10):
            await iface.press("delete")
            await asyncio.sleep(delay * 0.05)

    async def click_and_type(x: int, y: int, text: str, action: Optional[Dict[str, Any]] = None) -> None:
        await iface.left_click(int(x), int(y))
        await asyncio.sleep(delay)
        await clear_field()
        if text:
            await iface.type_text(text)
        await asyncio.sleep(delay)
        if recorder is not None and action is not None:
            recorder.append(action)

    async def type_in_place(text: str, action: Optional[Dict[str, Any]] = None) -> None:
        await clear_field()
        if text:
            await iface.type_text(text)
        await asyncio.sleep(delay)
        if recorder is not None and action is not None:
            recorder.append(action)

    # Fill scalar fields: tab-walk top row fields, then use coords for base_rate/drg_version
    scalar_tab_keys = [
        "gender",
        "age",
        "age_type",
        "admission_weight",
        "ventilation_time",
        "length_of_stay",
        "department_type",
        "admission_type",
        "admission_reason",
        "discharge_type",
    ]
    scalar_coord_keys = ["base_rate", "drg_version"]
    start = get_coord("gender")
    if use_tab_walk and start:
        # Seed on gender first
        gender_txt = fmt_value(data.get("gender", ""))
        print(f"[macro] gender (seed) -> ({start['x']},{start['y']}) '{gender_txt}'")
        await click_and_type(int(start["x"]), int(start["y"]), gender_txt, {"op": "field", "key": "gender"})
        # Confirm the dropdown value, then move forward
        await iface.press("enter")
        await asyncio.sleep(delay)
        await iface.press("tab")
        await asyncio.sleep(delay)
        # Then tab-walk the remaining scalar fields (exclude gender)
        for key in scalar_tab_keys[1:]:
            txt = fmt_value(data.get(key, ""))
            print(f"[macro] {key} (tab-walk) -> '{txt}'")
            await clear_field()
            if txt:
                await iface.type_text(txt)
            await asyncio.sleep(delay)
            await iface.press("tab")
            await asyncio.sleep(delay)
            if recorder is not None:
                recorder.append({"op": "field", "key": key})
        # Fill base_rate/drg_version by coord to avoid tab-walk drift
        for key in scalar_coord_keys:
            c = get_coord(key)
            if not c:
                print(f"[macro] missing coord for {key}, skipping")
                continue
            txt = fmt_value(data.get(key, ""))
            print(f"[macro] {key} (coord) -> ({c['x']},{c['y']}) '{txt}'")
            await click_and_type(int(c["x"]), int(c["y"]), txt, {"op": "field", "key": key})
    else:
        # Per-field coords
        for key in scalar_tab_keys + scalar_coord_keys:
            c = get_coord(key)
            if not c:
                print(f"[macro] missing coord for {key}, skipping")
                continue
            txt = fmt_value(data.get(key, ""))
            print(f"[macro] {key} -> ({c['x']},{c['y']}) '{txt}'")
            await click_and_type(int(c["x"]), int(c["y"]), txt, {"op": "field", "key": key})

    # Diagnoses: click each cell directly (no tab-walk)
    diags = data.get("diagnoses", [])
    if diag_cells:
        for i, d in enumerate(diags):
            if i >= len(diag_cells):
                print(f"[macro] no coord for diagnoses[{i}], skipping")
                break
            txt = fmt_value(d)
            cell = diag_cells[i]
            print(f"[macro] diagnoses[{i}] -> ({cell['x']},{cell['y']}) '{txt}'")
            await click_and_type(int(cell["x"]), int(cell["y"]), txt, {"op": "diagnosis", "index": i})
    else:
        print("[macro] no diagnosis_cells; skipping diagnoses")

    # Procedures with dates: click code cell, type, then Tab to date field (it appears after code)
    procs = data.get("procedures", [])
    dates = data.get("procedure_dates", [])
    if proc_rows and procs:
        for i, p in enumerate(procs):
            if i >= len(proc_rows):
                print(f"[macro] no coord for procedures[{i}], skipping")
                break
            row = proc_rows[i]
            code_cell = row.get("code")
            p_txt = fmt_value(p)
            d_txt = fmt_value(dates[i]) if i < len(dates) else ""
            if code_cell:
                print(f"[macro] procedures[{i}] code -> ({code_cell['x']},{code_cell['y']}) '{p_txt}'")
                await click_and_type(int(code_cell["x"]), int(code_cell["y"]), p_txt, {"op": "procedure_code", "index": i})
            if d_txt:
                print(f"[macro] procedures[{i}] date (tab) -> '{d_txt}'")
                await iface.press("tab")
                await asyncio.sleep(delay)
                await type_in_place(d_txt, {"op": "procedure_date", "index": i})
    else:
        print("[macro] no procedure_rows; skipping procedures")

    # Submit intentionally skipped until verification passes
    print("[macro] done (submit skipped).")


async def apply_fixes(computer: Computer, data: Dict[str, Any], coords: Dict[str, Any], fixes: Dict[str, Any], delay: float) -> None:
    """Apply targeted fixes based on FIX_ACTIONS."""
    iface = computer._interface  # type: ignore[attr-defined]
    if iface is None:
        raise RuntimeError("Computer interface not initialized.")

    fields = coords.get("fields", [])
    diag_cells = coords.get("diagnosis_cells", [])
    proc_rows = coords.get("procedure_rows", [])

    def get_coord(key: str) -> Optional[Dict[str, Any]]:
        for f in fields:
            if f.get("key") == key:
                return f
        return None

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

    # Fix diagnoses
    for dfix in fixes.get("diagnoses", []):
        idx = dfix.get("index")
        value = dfix.get("value", "")
        if idx is None or idx >= len(diag_cells):
            print(f"[fix] skip diagnoses[{idx}]")
            continue
        cell = diag_cells[idx]
        print(f"[fix] diagnoses[{idx}] -> ({cell['x']},{cell['y']}) '{value}'")
        await click_and_type(int(cell["x"]), int(cell["y"]), value)

    # Fix procedures
    for pfix in fixes.get("procedures", []):
        idx = pfix.get("index")
        code = pfix.get("code", "")
        date = pfix.get("date", "")
        if idx is None or idx >= len(proc_rows):
            print(f"[fix] skip procedures[{idx}]")
            continue
        row = proc_rows[idx]
        code_cell = row.get("code")
        date_cell = row.get("date")
        if code_cell:
            print(f"[fix] procedures[{idx}] code -> ({code_cell['x']},{code_cell['y']}) '{code}'")
            await click_and_type(int(code_cell["x"]), int(code_cell["y"]), code)
        if date_cell is not None:
            print(f"[fix] procedures[{idx}] date -> ({date_cell['x']},{date_cell['y']}) '{date}'")
            await click_and_type(int(date_cell["x"]), int(date_cell["y"]), date)

    # Fix scalar fields
    for f in fixes.get("fields", []):
        key = f.get("key")
        value = f.get("value", "")
        c = get_coord(key) if key else None
        if c:
            print(f"[fix] field {key} -> ({c['x']},{c['y']}) '{value}'")
            await click_and_type(int(c["x"]), int(c["y"]), value)

    print("[fix] done.")


async def submit_if_present(computer: Computer, coords: Dict[str, Any], delay: float) -> None:
    iface = computer._interface  # type: ignore[attr-defined]
    if iface is None:
        raise RuntimeError("Computer interface not initialized.")
    submit = coords.get("submit")
    if submit and "x" in submit and "y" in submit:
        sx = int(submit["x"])
        sy = int(submit["y"])
        print(f"[submit] clicking submit at ({sx},{sy})")
        await iface.left_click(sx, sy)
        await asyncio.sleep(delay)
    else:
        print("[submit] no submit coord; skipping submit click")


async def run_agent_verify(agent: ComputerAgent, messages: List[Dict[str, Any]], max_steps: int) -> Optional[Dict[str, Any]]:
    steps = 0
    fixes: Optional[Dict[str, Any]] = None
    async for result in agent.run(messages, stream=False):
        steps += 1
        for item in result.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    txt = part.get("text")
                    if txt:
                        print(f"[agent-verify] {txt}")
                        maybe = extract_fix_actions(txt)
                        if maybe:
                            fixes = maybe
        if fixes is not None:
            print("[verify] extracted FIX_ACTIONS.")
            break
        if steps >= max_steps:
            print(f"[verify] reached {max_steps} steps; stopping.")
            break
    return fixes


async def main_async(args: argparse.Namespace) -> None:
    load_dotenv_files()
    model = os.getenv("CUA_MODEL", "anthropic/claude-opus-4-5-20251101")
    host = os.getenv("CUA_HOST", "127.0.0.1")
    port = int(os.getenv("CUA_PORT", "8000"))

    payload = load_payload(args.data)

    async with Computer(os_type="linux", use_host_computer_server=True, host=host, port=port) as computer:
        # Builder agent (coords only)
        builder_agent = ComputerAgent(
            model=model,
            tools=[computer],
            only_n_most_recent_images=3,
            verbosity=logging.INFO,
            max_trajectory_budget=10.0,
        )
        coords = await run_agent_for_coords(
            builder_agent,
            build_builder_messages(args.url, payload),
            max_steps=args.builder_steps,
        )

        # Save coords
        os.makedirs(os.path.dirname(args.coords_out), exist_ok=True)
        with open(args.coords_out, "w", encoding="utf-8") as f:
            json.dump(coords, f, indent=2)
        print(f"[runner] saved coords to {args.coords_out}")

        # Macro execution
        macro_actions: List[Dict[str, Any]] = []
        await run_macro(computer, payload, coords, args.delay, args.tab_walk, macro_actions)

        # Verification agent (optional)
        if args.verify_steps > 0:
            verify_agent = ComputerAgent(
                model=model,
                tools=[computer],
                only_n_most_recent_images=2,
                verbosity=logging.INFO,
                max_trajectory_budget=3.0,
            )
            fixes = await run_agent_verify(
                verify_agent,
                build_verify_messages(payload),
                max_steps=args.verify_steps,
            )
            if fixes:
                print("[runner] applying fixes from verify.")
                await apply_fixes(computer, payload, coords, fixes, args.delay)
                # Re-verify once
                fixes2 = await run_agent_verify(
                    verify_agent,
                    build_verify_messages(payload),
                    max_steps=args.verify_steps,
                )
                if fixes2:
                    print("[runner] additional fixes still reported; review manually.")
                else:
                    # after fixes success, submit
                    await submit_if_present(computer, coords, args.delay)
            else:
                # no fixes needed; submit
                await submit_if_present(computer, coords, args.delay)
        else:
            print("[runner] verification skipped.")

        # Save recorded macro actions (high-level) for reuse
        macro_out = args.coords_out.replace(".json", "_actions.json")
        with open(macro_out, "w", encoding="utf-8") as f:
            json.dump({"actions": macro_actions}, f, indent=2)
        print(f"[runner] saved macro actions to {macro_out}")


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
