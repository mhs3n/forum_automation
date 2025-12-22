"""Orchestrate an LLM + macro alternating flow across DRG and Datalytics.

Flow:
  1) LLM opens DRG Grouper, maps coords (if needed).
  2) Macro fills DRG payload.
  3) LLM verifies; if mismatches, generates FIX_ACTIONS and macro applies them.
  4) LLM opens Datalytics + logs in.
  5) Macro fills Datalytics payload.
  6) LLM verifies; if mismatches, applies fixes; otherwise stop.

This uses existing helpers from flow_utils and the DRG-specific macro logic
from llm_coord_macro_onepass.
"""

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from agent import ComputerAgent
from computer import Computer

from flow_utils import extract_block, load_env_defaults, load_json, render_task, save_json

# Import DRG-specific helpers (script-local module).
import llm_coord_macro_onepass as drg_flow


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a DRG + Datalytics alternating LLM/macro flow.")
    p.add_argument("--drg-url", required=True, help="DRG Grouper URL.")
    p.add_argument("--drg-data", required=True, help="DRG payload JSON.")
    p.add_argument("--drg-coords", default="data/drg_coords.json", help="DRG coords JSON (read/write).")
    p.add_argument("--drg-actions-out", default="data/drg_actions.json", help="DRG macro actions out.")
    p.add_argument("--drg-fix-out", default="data/drg_fix_actions.json", help="DRG fix actions out.")
    p.add_argument("--datalytics-url", default=None, help="Datalytics URL.")
    p.add_argument("--datalytics-data", default=None, help="Datalytics payload JSON.")
    p.add_argument("--datalytics-coords", default="data/datalytics_coords.json", help="Datalytics coords JSON.")
    p.add_argument("--datalytics-actions-out", default="data/datalytics_actions.json", help="Datalytics macro actions out.")
    p.add_argument("--datalytics-fix-out", default="data/datalytics_fix_actions.json", help="Datalytics fix actions out.")
    p.add_argument("--datalytics-login-task", default=None, help="Optional login task prompt file.")
    p.add_argument("--datalytics-user-env", default="DATALYTICS_USER", help="Env var for datalytics username.")
    p.add_argument("--datalytics-pass-env", default="DATALYTICS_PASS", help="Env var for datalytics password.")
    p.add_argument("--builder-steps", type=int, default=60, help="Max steps for builder stage.")
    p.add_argument("--verify-steps", type=int, default=10, help="Max steps for verify stage.")
    p.add_argument("--max-fix-rounds", type=int, default=1, help="Max fix rounds per stage.")
    p.add_argument("--delay", type=float, default=0.35, help="Delay between macro actions.")
    p.add_argument("--tab-walk", action="store_true", default=True, help="Use tab-walk for DRG scalars.")
    p.add_argument("--no-tab-walk", dest="tab_walk", action="store_false", help="Disable DRG tab-walk.")
    p.add_argument("--force-coords", action="store_true", help="Force re-mapping coords even if file exists.")
    p.add_argument(
        "--recenter-before-fix",
        action="store_true",
        default=False,
        help="Recenter viewport before applying fixes (off by default).",
    )
    p.add_argument("--skip-datalytics", action="store_true", help="Skip datalytics stage.")
    return p.parse_args()


async def run_agent_until_tag(
    agent: ComputerAgent,
    messages: List[Dict[str, Any]],
    tag: str,
    max_steps: int,
) -> Optional[Dict[str, Any]]:
    steps = 0
    result_block: Optional[Dict[str, Any]] = None
    async for result in agent.run(messages, stream=False):
        steps += 1
        for item in result.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    txt = part.get("text")
                    if txt:
                        maybe = extract_block(txt, tag)
                        if maybe is not None:
                            result_block = maybe
        if result_block is not None or steps >= max_steps:
            break
    return result_block


def build_datalytics_builder_messages(url: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    diag_count = len(payload.get("diagnoses", []))
    proc_count = len(payload.get("procedures", []))
    return [
        {
            "role": "user",
            "content": (
                "You are mapping coordinates for the Datalytics DRG/PEPP Grouper form. Rules:\n"
                "- Use at most 2 screenshots total.\n"
                "- Do NOT fill any data values (only add empty rows if required).\n"
                "- Normalize viewport (zoom 80%, scroll to top, then to a stable view) and keep it fixed.\n"
                "- If there is a 'Diagnose hinzufügen' button, click it until there are exactly "
                f"{diag_count} diagnosis rows visible.\n"
                "- If there is a 'Prozedur hinzufügen' button, click it until there are exactly "
                f"{proc_count} procedure rows visible (each with code+date inputs).\n"
                "- Capture coordinates for each diagnosis row input (top-to-bottom).\n"
                "- Capture coordinates for each procedure row: code input and date input.\n"
                "- Capture coordinates for the add buttons if present.\n"
                "- Output ONLY JSON wrapped in [[[COORDS_JSON]]] ... [[[END]]].\n"
                "JSON format:\n"
                "{\n"
                '  "fields": [ {"key": "<json_key>", "x": <int>, "y": <int>}, ... ],\n'
                '  "diagnosis_add_button": {"x": <int>, "y": <int>},\n'
                '  "procedure_add_button": {"x": <int>, "y": <int>},\n'
                '  "diagnosis_cells": [ {"x": <int>, "y": <int>}, ... ],\n'
                '  "procedure_rows": [ {"code": {"x": <int>, "y": <int>}, "date": {"x": <int>, "y": <int>}}, ... ],\n'
                '  "submit": {"x": <int>, "y": <int>}  // optional\n'
                "}\n"
                f"Use ONLY these keys for fields: {list(payload.keys())}\n"
                f"Target URL: {url}\n"
                "Do not invent values; just provide coords."
            ),
        }
    ]


async def apply_datalytics_fill(
    computer: Computer,
    payload: Dict[str, Any],
    coords: Dict[str, Any],
    delay: float,
    recorder: Optional[List[Dict[str, Any]]] = None,
) -> None:
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

    async def click_and_type(x: int, y: int, text: str, action: Optional[Dict[str, Any]] = None) -> None:
        await iface.left_click(int(x), int(y))
        await asyncio.sleep(delay)
        await clear_field()
        if text:
            await iface.type_text(text)
        await asyncio.sleep(delay)
        if recorder is not None and action is not None:
            recorder.append(action)

    # Fill scalar fields by coords
    for field in coords.get("fields", []):
        key = field.get("key")
        if not key:
            continue
        x = field.get("x")
        y = field.get("y")
        if x is None or y is None:
            continue
        value = fmt_value(payload.get(key, ""))
        print(f"[macro] {key} -> ({x},{y}) '{value}'")
        await click_and_type(int(x), int(y), value, {"op": "field", "key": key})

    # Add diagnosis rows if needed
    diag_cells = coords.get("diagnosis_cells", [])
    diag_add = coords.get("diagnosis_add_button")
    diags = payload.get("diagnoses", [])
    if diag_add and len(diags) > len(diag_cells):
        for _ in range(len(diags) - len(diag_cells)):
            await iface.left_click(int(diag_add["x"]), int(diag_add["y"]))
            await asyncio.sleep(delay)

    # Fill diagnoses
    if diag_cells:
        for i, d in enumerate(diags):
            if i >= len(diag_cells):
                print(f"[macro] no coord for diagnoses[{i}], skipping")
                break
            cell = diag_cells[i]
            txt = fmt_value(d)
            print(f"[macro] diagnoses[{i}] -> ({cell['x']},{cell['y']}) '{txt}'")
            await click_and_type(int(cell["x"]), int(cell["y"]), txt, {"op": "diagnosis", "index": i})
    else:
        print("[macro] no diagnosis_cells; skipping diagnoses")

    # Add procedure rows if needed
    proc_rows = coords.get("procedure_rows", [])
    proc_add = coords.get("procedure_add_button")
    procs = payload.get("procedures", [])
    if proc_add and len(procs) > len(proc_rows):
        for _ in range(len(procs) - len(proc_rows)):
            await iface.left_click(int(proc_add["x"]), int(proc_add["y"]))
            await asyncio.sleep(delay)

    # Fill procedures
    dates = payload.get("procedure_dates", [])
    if proc_rows and procs:
        for i, p in enumerate(procs):
            if i >= len(proc_rows):
                print(f"[macro] no coord for procedures[{i}], skipping")
                break
            row = proc_rows[i]
            code_cell = row.get("code")
            date_cell = row.get("date")
            p_txt = fmt_value(p)
            d_txt = fmt_value(dates[i]) if i < len(dates) else ""
            if code_cell:
                print(f"[macro] procedures[{i}] code -> ({code_cell['x']},{code_cell['y']}) '{p_txt}'")
                await click_and_type(int(code_cell["x"]), int(code_cell["y"]), p_txt, {"op": "procedure_code", "index": i})
            if date_cell:
                print(f"[macro] procedures[{i}] date -> ({date_cell['x']},{date_cell['y']}) '{d_txt}'")
                await click_and_type(int(date_cell["x"]), int(date_cell["y"]), d_txt, {"op": "procedure_date", "index": i})
    else:
        print("[macro] no procedure_rows; skipping procedures")

    print("[macro] datalytics fill done (submit skipped).")


async def recenter_view(
    agent: ComputerAgent,
    url: str,
    max_steps: int,
    label: str,
) -> bool:
    prompt = (
        f"Return to the stable form view used for coordinate-based macros. "
        f"Normalize the viewport (zoom 80%), scroll to the top, then to the stable view "
        f"where the mapped fields are visible. "
        f"URL: {url}. "
        f"When ready, respond ONLY with [[[READY]]] {{\"ready\": true}} [[[END]]]."
    )
    ready = await run_agent_until_tag(
        agent,
        [{"role": "user", "content": prompt}],
        tag="READY",
        max_steps=max_steps,
    )
    if not ready:
        print(f"[{label}] recenter failed; coords may be off.")
        return False
    return True


async def run_drg_stage(
    computer: Computer,
    model: str,
    url: str,
    payload: Dict[str, Any],
    coords_path: str,
    actions_out: str,
    fix_out: str,
    builder_steps: int,
    verify_steps: int,
    max_fix_rounds: int,
    delay: float,
    tab_walk: bool,
    force_coords: bool,
    recenter_before_fix: bool,
) -> bool:
    coords = None
    if not force_coords and os.path.exists(coords_path):
        coords = load_json(coords_path)
        print(f"[drg] using existing coords from {coords_path} (use --force-coords to remap)")
        opener_agent = ComputerAgent(
            model=model,
            tools=[computer],
            only_n_most_recent_images=2,
            max_trajectory_budget=5.0,
        )
        ready_prompt = (
            "Open the DRG Grouper URL, normalize the viewport (zoom 80%, scroll to top, then to the stable form view), "
            "and wait until the form is visible. "
            f"URL: {url}. "
            "When ready, respond ONLY with [[[READY]]] {\"ready\": true} [[[END]]]."
        )
        ready = await run_agent_until_tag(
            opener_agent,
            [{"role": "user", "content": ready_prompt}],
            tag="READY",
            max_steps=builder_steps,
        )
        if not ready:
            print("[drg] opener did not confirm readiness; stopping.")
            return False
    else:
        builder_agent = ComputerAgent(
            model=model,
            tools=[computer],
            only_n_most_recent_images=3,
            max_trajectory_budget=10.0,
        )
        coords = await drg_flow.run_agent_for_coords(
            builder_agent,
            drg_flow.build_builder_messages(url, payload),
            max_steps=builder_steps,
        )
        save_json(coords_path, coords)
        print(f"[drg] saved coords to {coords_path}")

    macro_actions: List[Dict[str, Any]] = []
    await drg_flow.apply_fill(computer, payload, coords, delay, tab_walk, macro_actions)
    save_json(actions_out, {"actions": macro_actions})
    print(f"[drg] saved macro actions to {actions_out}")

    if verify_steps <= 0:
        print("[drg] verify skipped.")
        return True

    verify_agent = ComputerAgent(
        model=model,
        tools=[computer],
        only_n_most_recent_images=2,
        max_trajectory_budget=3.0,
    )

    for round_idx in range(max_fix_rounds + 1):
        fixes = await drg_flow.run_agent_verify(
            verify_agent,
            drg_flow.build_verify_messages(payload),
            max_steps=verify_steps,
        )
        if not fixes:
            await drg_flow.submit_if_present(computer, coords, delay)
            return True
        save_json(fix_out, fixes)
        print(f"[drg] saved fix actions to {fix_out}")
        if round_idx >= max_fix_rounds:
            break
        if recenter_before_fix:
            await recenter_view(verify_agent, url, builder_steps, "drg")
        await drg_flow.apply_fixes(computer, payload, coords, fixes, delay)

    print("[drg] unresolved mismatches; stopping before datalytics.")
    return False


async def run_datalytics_stage(
    computer: Computer,
    model: str,
    url: str,
    payload: Dict[str, Any],
    coords_path: str,
    actions_out: str,
    fix_out: str,
    login_task: str,
    builder_steps: int,
    verify_steps: int,
    max_fix_rounds: int,
    delay: float,
    force_coords: bool,
    tab_walk: bool,
    recenter_before_fix: bool,
) -> bool:
    login_agent = ComputerAgent(
        model=model,
        tools=[computer],
        only_n_most_recent_images=2,
        max_trajectory_budget=5.0,
    )
    ready = await run_agent_until_tag(
        login_agent,
        [{"role": "user", "content": login_task}],
        tag="READY",
        max_steps=builder_steps,
    )
    if not ready:
        print("[datalytics] login/open not confirmed; stopping.")
        return False

    coords = None
    if not force_coords and os.path.exists(coords_path):
        coords = load_json(coords_path)
        print(f"[datalytics] using existing coords from {coords_path} (use --force-coords to remap)")
    else:
        builder_agent = ComputerAgent(
            model=model,
            tools=[computer],
            only_n_most_recent_images=3,
            max_trajectory_budget=10.0,
        )
        coords = await drg_flow.run_agent_for_coords(
            builder_agent,
            build_datalytics_builder_messages(url, payload),
            max_steps=builder_steps,
        )
        save_json(coords_path, coords)
        print(f"[datalytics] saved coords to {coords_path}")

    macro_actions: List[Dict[str, Any]] = []
    await apply_datalytics_fill(computer, payload, coords, delay, macro_actions)
    save_json(actions_out, {"actions": macro_actions})
    print(f"[datalytics] saved macro actions to {actions_out}")

    if verify_steps <= 0:
        print("[datalytics] verify skipped.")
        return True

    verify_agent = ComputerAgent(
        model=model,
        tools=[computer],
        only_n_most_recent_images=2,
        max_trajectory_budget=3.0,
    )
    for round_idx in range(max_fix_rounds + 1):
        fixes = await drg_flow.run_agent_verify(
            verify_agent,
            drg_flow.build_verify_messages(payload),
            max_steps=verify_steps,
        )
        if not fixes:
            return True
        save_json(fix_out, fixes)
        print(f"[datalytics] saved fix actions to {fix_out}")
        if round_idx >= max_fix_rounds:
            break
        if recenter_before_fix:
            await recenter_view(verify_agent, url, builder_steps, "datalytics")
        await drg_flow.apply_fixes(computer, payload, coords, fixes, delay)

    print("[datalytics] unresolved mismatches.")
    return False


def default_datalytics_login_prompt(url: str) -> str:
    return (
        "Open a NEW TAB (click the '+' tab button; if not visible, use Ctrl+T) and keep the current tab unchanged. "
        "In the new tab, click the address bar, type the URL, press Enter, and wait for the page to load. "
        "Use clicks for navigation (no Ctrl+L). "
        "If a login form appears, click the username field, type <USER>, press Tab, type <PASS>, press Enter. "
        "Wait until the main page loads. "
        f"URL: {url}. "
        "When ready, respond ONLY with [[[READY]]] {\"ready\": true} [[[END]]]."
    )


async def main_async(args: argparse.Namespace) -> None:
    model, host, port = load_env_defaults()

    drg_payload = load_json(args.drg_data)
    datalytics_payload = load_json(args.datalytics_data) if args.datalytics_data else None

    async with Computer(os_type="linux", use_host_computer_server=True, host=host, port=port) as computer:
        ok = await run_drg_stage(
            computer=computer,
            model=model,
            url=args.drg_url,
            payload=drg_payload,
            coords_path=args.drg_coords,
            actions_out=args.drg_actions_out,
            fix_out=args.drg_fix_out,
            builder_steps=args.builder_steps,
            verify_steps=args.verify_steps,
            max_fix_rounds=args.max_fix_rounds,
            delay=args.delay,
            tab_walk=args.tab_walk,
            force_coords=args.force_coords,
            recenter_before_fix=args.recenter_before_fix,
        )
        if not ok or args.skip_datalytics:
            return

        if not args.datalytics_url or not datalytics_payload:
            print("[flow] datalytics config missing; skipping.")
            return

        user = os.getenv(args.datalytics_user_env)
        password = os.getenv(args.datalytics_pass_env)
        if not user or not password:
            raise RuntimeError("Missing datalytics credentials in env.")

        if args.datalytics_login_task:
            with open(args.datalytics_login_task, "r", encoding="utf-8") as f:
                login_task = f.read().strip()
        else:
            login_task = default_datalytics_login_prompt(args.datalytics_url)
        login_task = render_task(login_task, {"USER": user, "PASS": password})

        await run_datalytics_stage(
            computer=computer,
            model=model,
            url=args.datalytics_url,
            payload=datalytics_payload,
            coords_path=args.datalytics_coords,
            actions_out=args.datalytics_actions_out,
            fix_out=args.datalytics_fix_out,
            login_task=login_task,
            builder_steps=args.builder_steps,
            verify_steps=args.verify_steps,
            max_fix_rounds=args.max_fix_rounds,
            delay=args.delay,
            force_coords=args.force_coords,
            tab_walk=args.tab_walk,
            recenter_before_fix=args.recenter_before_fix,
        )


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
