"""Reusable helpers for LLM-driven CUA flows and macro replay."""

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from agent import ComputerAgent
from computer import Computer
from utils import load_dotenv_files


def load_env_defaults() -> Tuple[str, str, int]:
    """Load env and return (model, host, port)."""
    load_dotenv_files()
    model = os.getenv("CUA_MODEL", "anthropic/claude-opus-4-5-20251101")
    host = os.getenv("CUA_HOST", "127.0.0.1")
    port = int(os.getenv("CUA_PORT", "8000"))
    return model, host, port


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def render_task(template: str, replacements: Dict[str, str]) -> str:
    """Replace <PLACEHOLDER> tokens in the task template."""
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(f"<{key.upper()}>", value)
    return rendered


def extract_block(text: str, tag: str) -> Optional[Dict[str, Any]]:
    """Extract JSON between [[[TAG]]] and [[[END]]]."""
    pattern = rf"\[\[\[{re.escape(tag)}\]\]\](.*?)\[\[\[END\]\]\]"
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        return None
    block = m.group(1).strip()
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        return None


def mask_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mask typed text in actions."""
    masked: List[Dict[str, Any]] = []
    for act in actions:
        if act.get("type") in {"type", "type_text"} and "text" in act:
            act = dict(act)
            act["text"] = "<MASKED>"
        masked.append(act)
    return masked


def filter_actions(actions: List[Dict[str, Any]], keep_screens: bool) -> List[Dict[str, Any]]:
    """Drop screenshot/wait/scroll if keep_screens is False."""
    if keep_screens:
        return actions
    filtered = []
    for act in actions:
        if act.get("type") in {"screenshot", "wait", "scroll"}:
            continue
        filtered.append(act)
    return filtered


async def run_llm_task(
    task: str,
    max_steps: int = 200,
    macro_tag: Optional[str] = "MACRO",
    keep_screens: bool = False,
    mask_text: bool = False,
    only_n_most_recent_images: int = 3,
    max_trajectory_budget: float = 10.0,
    verbose: bool = False,
    log_actions: bool = False,
    log_messages: bool = False,
    stop_event: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run an LLM task and capture macro block + raw actions."""
    if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
        return {"macro": None, "actions": [], "stopped": True}
    model, host, port = load_env_defaults()
    messages = [{"role": "user", "content": task}]
    steps = 0
    macro: Optional[Dict[str, Any]] = None
    raw_actions: List[Dict[str, Any]] = []
    stopped = False

    async with Computer(
        os_type="windows",
        use_host_computer_server=True,
        api_host=host,
        api_port=port,
    ) as computer:
        agent = ComputerAgent(
            model=model,
            tools=[computer],
            only_n_most_recent_images=only_n_most_recent_images,
            max_trajectory_budget=max_trajectory_budget,
        )

        async for result in agent.run(messages, stream=False):
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                stopped = True
                break
            steps += 1
            if verbose:
                print(f"[llm] step {steps}")
            for item in result.get("output", []):
                if item.get("type") == "message" and macro_tag:
                    for part in item.get("content", []):
                        txt = part.get("text")
                        if txt and log_messages:
                            print(f"[llm] message: {txt}")
                        if txt:
                            maybe = extract_block(txt, macro_tag)
                            if maybe:
                                macro = maybe
                elif item.get("type") == "message" and log_messages and not macro_tag:
                    for part in item.get("content", []):
                        txt = part.get("text")
                        if txt:
                            print(f"[llm] message: {txt}")
                elif item.get("type") == "computer_call":
                    action = item.get("action", {})
                    if mask_text:
                        action = mask_actions([action])[0]
                    raw_actions.append(action)
                    if log_actions:
                        op = action.get("type") or action.get("action") or action.get("op")
                        if op in ("click", "left_click"):
                            x = action.get("x")
                            y = action.get("y")
                            print(f"[llm] action: click ({x},{y})")
                        elif op in ("type", "type_text"):
                            txt = action.get("text", "")
                            print(f"[llm] action: type '{txt}'")
                        elif op in ("keypress", "press"):
                            keys = action.get("keys") or action.get("key") or []
                            print(f"[llm] action: press {keys}")
                        elif op == "wait":
                            secs = action.get("seconds", "")
                            print(f"[llm] action: wait {secs}")
                        else:
                            print(f"[llm] action: {op}")
            if macro:
                break
            if steps >= max_steps:
                break

    raw_actions = filter_actions(raw_actions, keep_screens)
    return {"macro": macro, "actions": raw_actions, "stopped": stopped}


async def replay_actions(
    actions: List[Dict[str, Any]],
    delay: float = 0.35,
    email: Optional[str] = None,
    password: Optional[str] = None,
    stop_event: Optional[Any] = None,
) -> None:
    """Replay raw actions via the CUA interface."""
    model, host, port = load_env_defaults()
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
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                break
            op = act.get("type") or act.get("action") or act.get("op")
            if op in ("click", "left_click"):
                x = act.get("x")
                y = act.get("y")
                if x is not None and y is not None:
                    await iface.left_click(int(x), int(y))
            elif op == "type" or op == "type_text":
                txt = act.get("text", "")
                if txt == "<MASKED>":
                    if email:
                        txt = email
                        email = None
                    elif password:
                        txt = password
                        password = None
                await iface.type_text(str(txt))
            elif op == "keypress" or op == "press":
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
