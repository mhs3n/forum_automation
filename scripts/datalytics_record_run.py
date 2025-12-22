"""Run a Datalytics fill session with the LLM and record two macros:
1) Raw computer_call actions (comprehensive replay).
2) LLM-emitted macro JSON at the end of the task.
"""

import argparse
import asyncio
import logging
import os
import sys
from typing import Dict

from flow_utils import load_json, mask_actions, render_task, run_llm_task, save_json


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record a Datalytics LLM run into replayable macros.")
    p.add_argument(
        "--url",
        default="https://datalytics.drg-pepp.de/drg-pepp-grouper/grouping",
        help="Datalytics URL (MANDATORY; do not change unless site URL changes).",
    )
    p.add_argument("--data", required=True, help="Path to payload JSON.")
    p.add_argument("--user", default=None, help="Username (overrides env).")
    p.add_argument("--password", default=None, help="Password (overrides env).")
    p.add_argument("--user-env", default="DATALYTICS_USER", help="Env var for username.")
    p.add_argument("--pass-env", default="DATALYTICS_PASS", help="Env var for password.")
    p.add_argument("--out-llm", default="trajectories/datalytics_macro_llm.json", help="LLM macro output path.")
    p.add_argument("--out-raw", default="trajectories/datalytics_actions_raw.json", help="Raw actions output path.")
    p.add_argument("--max-steps", type=int, default=240, help="Max steps for the LLM run.")
    p.add_argument("--keep-screens", action="store_true", help="Keep screenshots/waits in raw actions.")
    p.add_argument("--mask-text", action="store_true", default=True, help="Mask typed text in recordings (default: on).")
    p.add_argument("--no-mask", dest="mask_text", action="store_false", help="Disable masking.")
    p.add_argument("--submit", action="store_true", help="Click submit after filling.")
    return p.parse_args()


def build_task(url: str, payload: Dict, submit: bool) -> str:
    diag_count = len(payload.get("diagnoses", []))
    proc_count = len(payload.get("procedures", []))
    submit_line = "Finally, click the submit/start grouping button." if submit else "Do NOT submit the form."
    return (
        "You are controlling the browser. Use clicks + typing (no Ctrl+L). "
        f"MANDATORY URL: {url} . You MUST open exactly this URL (no localhost, no suggestions). "
        "Click the address bar, press Ctrl+A, then Backspace 10x and Delete 10x to clear it, "
        "type the full URL above, press Enter, and wait for the page. Ignore autocomplete. "
        "Log in with <USER>/<PASS> if prompted. "
        "Fill the form with this payload:\n"
        f"{payload}\n"
        f"- You must end with {diag_count} diagnoses and {proc_count} procedures filled.\n"
        "- Use 'Diagnose hinzufügen' / 'Prozedur hinzufügen' buttons to create enough rows if needed.\n"
        "- Before typing in any input, click it and clear existing content (Ctrl+A, Backspace 10x, Delete 10x).\n"
        f"{submit_line} "
        "Do NOT emit the macro until ALL payload fields, diagnoses, and procedures are populated and visible. "
        "When completely finished, output ONLY the macro JSON of all actions you performed (include coordinates and scrolls), "
        "wrapped in [[[MACRO]]] ... [[[END]]], like:\n"
        "[[[MACRO]]]\n"
        "{ \"actions\": [ "
        "{\"type\":\"click\",\"x\":1,\"y\":1}, "
        "{\"type\":\"type\",\"text\":\"https://datalytics.drg-pepp.de/drg-pepp-grouper/grouping\"}, "
        "{\"type\":\"type\",\"text\":\"{{USER}}\"}, "
        "{\"type\":\"type\",\"text\":\"{{FIELD.age}}\"}, "
        "{\"type\":\"type\",\"text\":\"{{DIAG.0}}\"}, "
        "{\"type\":\"type\",\"text\":\"{{PROC.0}}\"}, "
        "{\"type\":\"type\",\"text\":\"{{DATE.0}}\"}"
        " ] }\n"
        "[[[END]]]"
    )


async def main_async(args: argparse.Namespace) -> None:
    payload = load_json(args.data)
    user = args.user or os.getenv(args.user_env)
    password = args.password or os.getenv(args.pass_env)
    if not user or not password:
        raise RuntimeError("Missing Datalytics credentials (use --user/--password or env vars).")

    task = build_task(args.url, payload, args.submit)
    task = render_task(task, {"USER": user, "PASS": password})

    result = await run_llm_task(
        task=task,
        max_steps=args.max_steps,
        macro_tag="MACRO",
        keep_screens=args.keep_screens,
        mask_text=args.mask_text,
    )

    llm_macro = result.get("macro") or {"actions": []}
    raw_actions = result.get("actions") or []

    if args.mask_text:
        llm_macro = dict(llm_macro)
        llm_macro["actions"] = mask_actions(llm_macro.get("actions", []))

    save_json(args.out_llm, llm_macro)
    print(f"[record] saved LLM macro to {args.out_llm}")

    save_json(args.out_raw, {"actions": raw_actions})
    print(f"[record] saved raw actions to {args.out_raw}")


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
