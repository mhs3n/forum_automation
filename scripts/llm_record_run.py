"""Run an LLM-driven session for a custom task and record raw computer actions to JSON.

Example:
  python scripts/llm_record_run.py \
    --task "Open linkedin.com, sign in with the provided credentials, then go to Jobs tab."

Notes:
  - This records raw computer_call actions (clicks/keypress/type). It will not be perfect
    for highly dynamic sites, but you can replay as a starting point.
  - Credentials should be provided at runtime (e.g., via prompt/environment); do NOT hardcode.
"""

import argparse
import asyncio
import logging
import sys
from typing import Optional

from flow_utils import mask_actions, render_task, run_llm_task, save_json


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run an LLM task and capture a macro JSON emitted by the LLM.")
    p.add_argument(
        "--task",
        default=None,
        help="Task instruction for the LLM. If omitted, a built-in LinkedIn login->Jobs task is used.",
    )
    p.add_argument("--task-file", default=None, help="Path to a text file containing the task prompt.")
    p.add_argument("--email", default=None, help="Optional email to substitute for <EMAIL> in the task.")
    p.add_argument("--password", default=None, help="Optional password to substitute for <PASSWORD> in the task.")
    p.add_argument("--out", default="trajectories/recorded_macro.json", help="Where to save the macro JSON.")
    p.add_argument("--actions-out", default="trajectories/recorded_actions.json", help="Where to save the raw computer_call actions.")
    p.add_argument("--max-steps", type=int, default=200, help="Max agent steps (default 200).")
    p.add_argument(
        "--keep-screens",
        action="store_true",
        help="Keep screenshot/wait/scroll actions (default: drop them to keep the macro lean).",
    )
    p.add_argument(
        "--mask-text",
        action="store_true",
        help="Mask typed text values in the recording (avoids storing secrets).",
    )
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> None:
    default_task = (
        "You are controlling the browser. Perform these steps with clicks (no keyboard shortcuts): "
        "1) Click the address bar, type https://www.linkedin.com/login, press Enter, and wait for the login page. "
        "2) Click the email box, type <EMAIL>, press Tab, type <PASSWORD>, press Enter to sign in. "
        "3) Wait for the home page, then click the Jobs tab in the top navigation. "
        "When finished, output ONLY the macro JSON of all the actions you performed with coordinates, wrapped in [[[MACRO]]] ... [[[END]]], like: "
        "[[[MACRO]]]\n"
        "{ \"actions\": [ "
        "{\"type\":\"click\",\"x\":...,\"y\":...}, "
        "{\"type\":\"type\",\"text\":\"https://www.linkedin.com/login\"}, "
        "{\"type\":\"keypress\",\"keys\":[\"Return\"]}, "
        "{\"type\":\"wait\",\"seconds\":5}, "
        "{\"type\":\"click\",\"x\":...,\"y\":...}, "
        "{\"type\":\"type\",\"text\":\"<EMAIL>\"}, "
        "{\"type\":\"keypress\",\"keys\":[\"Tab\"]}, "
        "{\"type\":\"type\",\"text\":\"<PASSWORD>\"}, "
        "{\"type\":\"keypress\",\"keys\":[\"Return\"]}, "
        "{\"type\":\"wait\",\"seconds\":5}, "
        "{\"type\":\"click\",\"x\":...,\"y\":...} "
        "] }\n"
        "[[[END]]]\n"
        "Do not include other text."
    )
    if args.task_file:
        with open(args.task_file, "r", encoding="utf-8") as f:
            task = f.read().strip()
    else:
        task = args.task or default_task

    replacements = {}
    if args.email:
        replacements["EMAIL"] = args.email
    if args.password:
        replacements["PASSWORD"] = args.password
    if replacements:
        task = render_task(task, replacements)
    result = await run_llm_task(
        task=task,
        max_steps=args.max_steps,
        macro_tag="MACRO",
        keep_screens=args.keep_screens,
        mask_text=args.mask_text,
    )
    macro = result.get("macro") or {"actions": []}
    raw_actions = result.get("actions") or []

    if args.mask_text:
        macro_actions = mask_actions(macro.get("actions", []))
        macro = dict(macro)
        macro["actions"] = macro_actions

    save_json(args.out, macro)
    print(f"[record] saved macro JSON to {args.out}")

    save_json(args.actions_out, {"actions": raw_actions})
    print(f"[record] saved raw actions to {args.actions_out}")


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
