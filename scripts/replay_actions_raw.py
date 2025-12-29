"""Replay raw recorded computer_call actions via the CUA interface."""

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
    p = argparse.ArgumentParser(description="Replay raw computer_call actions.")
    p.add_argument("--actions", required=True, help="Path to recorded actions JSON ({\"actions\": [...]})")
    p.add_argument("--delay", type=float, default=0.35, help="Delay between actions (seconds).")
    p.add_argument("--email", default=None, help="Optional email to substitute for <MASKED> values.")
    p.add_argument("--password", default=None, help="Optional password to substitute for <MASKED> values.")
    return p.parse_args()


def load_actions(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("actions", [])


async def replay(actions: List[Dict[str, Any]], delay: float, email: str = None, password: str = None) -> None:
    load_dotenv_files()
    host = os.getenv("CUA_HOST", "127.0.0.1")
    port = int(os.getenv("CUA_PORT", "8000"))
    async with Computer(
        os_type="windows",
        use_host_computer_server=True,
        api_host=host,
        api_port=port,
    ) as computer:
        iface = computer._interface  # type: ignore[attr-defined]
        if iface is None:
            raise RuntimeError("Computer interface not initialized.")

        for act in actions:
            op = act.get("type") or act.get("action") or act.get("op")
            if op in ("click", "left_click"):
                x = act.get("x")
                y = act.get("y")
                if x is not None and y is not None:
                    print(f"[replay] click ({x},{y})")
                    await iface.left_click(int(x), int(y))
            elif op == "type" or op == "type_text":
                txt = act.get("text", "")
                if txt == "<MASKED>":
                    # naive substitution: if email/password provided, prefer email first then password
                    if email:
                        txt = email
                        email = None  # use once
                    elif password:
                        txt = password
                        password = None
                print(f"[replay] type '{txt}'")
                await iface.type_text(str(txt))
            elif op == "keypress" or op == "press":
                keys = act.get("keys") or act.get("key") or []
                if isinstance(keys, str):
                    keys = [keys]
                print(f"[replay] press {keys}")
                for k in keys:
                    await iface.press(k)
            elif op == "wait":
                secs = act.get("seconds", delay)
                try:
                    secs = float(secs)
                except Exception:
                    secs = delay
                print(f"[replay] wait {secs}s")
                await asyncio.sleep(secs)
                continue
            # skip screenshots/others
            await asyncio.sleep(delay)
    print("[replay] done (submit not triggered here).")


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    actions = load_actions(args.actions)
    asyncio.run(replay(actions, args.delay))
    return 0


if __name__ == "__main__":
    sys.exit(main())
