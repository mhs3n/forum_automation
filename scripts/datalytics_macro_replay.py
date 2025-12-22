"""Replay a Datalytics macro with placeholders using a payload JSON."""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

from computer import Computer
from utils import load_dotenv_files


PLACEHOLDER_PATTERN = re.compile(r"\{\{([^}]+)\}\}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Replay a Datalytics macro with payload substitution.")
    p.add_argument("--macro", required=True, help="Path to LLM macro JSON.")
    p.add_argument("--data", required=True, help="Path to payload JSON.")
    p.add_argument("--url", default=None, help="Optional URL replacement for {{URL}}.")
    p.add_argument("--user", default=None, help="Username (overrides env).")
    p.add_argument("--password", default=None, help="Password (overrides env).")
    p.add_argument("--user-env", default="DATALYTICS_USER", help="Env var for username.")
    p.add_argument("--pass-env", default="DATALYTICS_PASS", help="Env var for password.")
    p.add_argument("--delay", type=float, default=0.5, help="Delay between actions.")
    p.add_argument("--auto-clear", action="store_true", help="Clear field before each type.")
    return p.parse_args()


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_text(text: str, payload: Dict[str, Any], user: str, password: str, url: Optional[str]) -> str:
    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        if key == "USER":
            return user or ""
        if key == "PASS":
            return password or ""
        if key == "URL":
            return url or ""
        if key.startswith("FIELD."):
            field = key.split(".", 1)[1]
            return str(payload.get(field, ""))
        if key.startswith("DIAG."):
            idx = int(key.split(".", 1)[1])
            return str(payload.get("diagnoses", [])[idx])
        if key.startswith("PROC."):
            idx = int(key.split(".", 1)[1])
            return str(payload.get("procedures", [])[idx])
        if key.startswith("DATE."):
            idx = int(key.split(".", 1)[1])
            return str(payload.get("procedure_dates", [])[idx])
        return match.group(0)

    return PLACEHOLDER_PATTERN.sub(repl, text)


async def replay(actions: List[Dict[str, Any]], payload: Dict[str, Any], user: str, password: str, url: Optional[str], delay: float, auto_clear: bool) -> None:
    load_dotenv_files()
    host = os.getenv("CUA_HOST", "127.0.0.1")
    port = int(os.getenv("CUA_PORT", "8000"))

    async with Computer(os_type="linux", use_host_computer_server=True, host=host, port=port) as computer:
        iface = computer._interface  # type: ignore[attr-defined]
        if iface is None:
            raise RuntimeError("Computer interface not initialized.")

        async def clear_field() -> None:
            await iface.press("ctrl+a")
            await asyncio.sleep(delay * 0.2)
            for _ in range(10):
                await iface.press("backspace")
                await asyncio.sleep(delay * 0.05)
            for _ in range(10):
                await iface.press("delete")
                await asyncio.sleep(delay * 0.05)

        for act in actions:
            op = act.get("type") or act.get("action") or act.get("op")
            if op in ("click", "left_click"):
                x = act.get("x")
                y = act.get("y")
                if x is not None and y is not None:
                    await iface.left_click(int(x), int(y))
            elif op in ("type", "type_text"):
                txt = str(act.get("text", ""))
                txt = resolve_text(txt, payload, user, password, url)
                if auto_clear:
                    await clear_field()
                await iface.type_text(txt)
            elif op in ("keypress", "press"):
                keys = act.get("keys") or act.get("key") or []
                if isinstance(keys, str):
                    keys = [keys]
                for k in keys:
                    await iface.press(k)
            elif op == "wait":
                secs = act.get("seconds", delay)
                try:
                    secs = float(secs)
                except Exception:
                    secs = delay
                await asyncio.sleep(secs)
                continue
            await asyncio.sleep(delay)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)

    payload = load_json(args.data)
    macro = load_json(args.macro)
    actions = macro.get("actions", [])

    user = args.user or os.getenv(args.user_env, "")
    password = args.password or os.getenv(args.pass_env, "")

    asyncio.run(replay(actions, payload, user, password, args.url, args.delay, args.auto_clear))
    return 0


if __name__ == "__main__":
    sys.exit(main())
