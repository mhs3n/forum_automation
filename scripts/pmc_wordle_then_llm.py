"""Run a PMC macro to open Wordle, then let the CUA LLM solve it (F12 stops)."""

import argparse
import asyncio
import os
import subprocess
import sys
import threading
import time
from getpass import getpass
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flow_utils import run_llm_task
from llm_tasks import build_wordle_solve_task
from pmc_utils import resolve_pmc_exe, resolve_pmc_file
from utils import load_dotenv_files


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PMC -> LLM Wordle test with F12 stop.")
    p.add_argument("--steps", default="pmc,llm", help="Comma-separated steps (pmc,llm).")
    p.add_argument("--pmc-file", default=None, help="Path to .pmc file.")
    p.add_argument("--pmc-exe", default=None, help="Path to MacroCreator.exe.")
    p.add_argument("--pmc-index", type=int, default=1, help="PMC macro index (1-based).")
    p.add_argument("--pmc-indices", default=None, help="Comma-separated macro indices for each pmc step.")
    p.add_argument("--pmc-wait", action="store_true", help="Wait for PMC to exit before continuing.")
    p.add_argument("--pmc-kill", action="store_true", help="Kill existing MacroCreator.exe before launch.")
    p.add_argument("--pmc-silent", action="store_true", help="Use PMC silent mode (-sN) instead of -aN.")
    p.add_argument("--no-close", action="store_true", help="Do not close PMC after macro runs.")
    p.add_argument("--post-step-wait", type=float, default=1.0, help="Seconds to wait between steps.")
    p.add_argument("--max-steps", type=int, default=200, help="Max LLM steps (default 200).")
    p.add_argument("--anthropic-key", default=None, help="Anthropic API key (overrides env).")
    p.add_argument("--prompt-key", action="store_true", help="Prompt for Anthropic API key.")
    p.add_argument("--verbose", action="store_true", help="Print LLM step progress.")
    p.add_argument("--log-actions", action="store_true", help="Print each LLM computer action.")
    p.add_argument("--log-messages", action="store_true", help="Print LLM text messages.")
    p.add_argument("--mask-text", action="store_true", help="Mask typed text in printed actions.")
    p.add_argument("--hotkey", default="f12", help="Global hotkey to stop everything.")
    return p.parse_args()


def parse_indices(raw: Optional[str], fallback: int) -> List[int]:
    if not raw:
        return [fallback]
    out: List[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        out.append(int(item))
    return out or [fallback]


def setup_hotkey(stop_event: threading.Event, hotkey: str):
    try:
        import keyboard  # type: ignore
    except Exception as exc:
        print(f"[hotkey] disabled (install 'keyboard' if needed): {exc}")
        return None, None

    def _on_hotkey():
        if not stop_event.is_set():
            print(f"[hotkey] {hotkey} pressed - stopping.")
            stop_event.set()

    handle = keyboard.add_hotkey(hotkey, _on_hotkey)
    return keyboard, handle


def run_pmc_step(
    pmc_exe: str,
    pmc_file: str,
    macro_index: int,
    close_after: bool,
    wait: bool,
    kill_existing: bool,
    silent: bool,
    stop_event: threading.Event,
) -> None:
    if kill_existing:
        subprocess.run(["taskkill", "/IM", "MacroCreator.exe", "/F"], check=False)

    flag = f"-s{macro_index}" if silent else f"-a{macro_index}"
    args = [pmc_exe, pmc_file, flag]
    if close_after and not silent:
        args.append("-c")
    print(f"[pmc] launch: {args}")
    proc = subprocess.Popen(args)

    def _kill_on_stop() -> None:
        stop_event.wait()
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], check=False)

    threading.Thread(target=_kill_on_stop, daemon=True).start()

    if wait:
        while proc.poll() is None:
            if stop_event.is_set():
                print("[pmc] stop requested.")
                break
            time.sleep(0.2)


async def run_llm_wordle(
    max_steps: int,
    verbose: bool,
    log_actions: bool,
    log_messages: bool,
    mask_text: bool,
    stop_event: threading.Event,
) -> None:
    task = build_wordle_solve_task()
    print("[flow] LLM solving Wordle...")
    result = await run_llm_task(
        task=task,
        max_steps=max_steps,
        macro_tag=None,
        verbose=verbose,
        log_actions=log_actions,
        log_messages=log_messages,
        mask_text=mask_text,
        stop_event=stop_event,
    )
    if result.get("stopped"):
        print("[flow] LLM stopped.")
        return
    print("[flow] LLM done.")


def main() -> int:
    args = parse_args()
    load_dotenv_files()
    if args.anthropic_key:
        os.environ["ANTHROPIC_API_KEY"] = args.anthropic_key
    elif args.prompt_key and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = getpass("ANTHROPIC_API_KEY: ")

    stop_event = threading.Event()
    keyboard_mod, hotkey_handle = setup_hotkey(stop_event, args.hotkey)

    steps = [s.strip().lower() for s in args.steps.split(",") if s.strip()]
    pmc_indices = parse_indices(args.pmc_indices, args.pmc_index)
    pmc_exe = None
    pmc_file = None
    pmc_step = 0

    try:
        for step in steps:
            if stop_event.is_set():
                print("[flow] stop requested.")
                break

            if step == "pmc":
                if pmc_exe is None:
                    pmc_exe = resolve_pmc_exe(args.pmc_exe)
                    pmc_file = resolve_pmc_file(args.pmc_file)
                idx = pmc_indices[min(pmc_step, len(pmc_indices) - 1)]
                pmc_step += 1
                run_pmc_step(
                    pmc_exe,
                    pmc_file,
                    idx,
                    close_after=not args.no_close,
                    wait=args.pmc_wait,
                    kill_existing=args.pmc_kill,
                    silent=args.pmc_silent,
                    stop_event=stop_event,
                )
                print("[flow] PMC step done.")
            elif step == "llm":
                asyncio.run(
                    run_llm_wordle(
                        args.max_steps,
                        args.verbose,
                        args.log_actions,
                        args.log_messages,
                        args.mask_text,
                        stop_event,
                    )
                )
            else:
                print(f"[flow] unknown step: {step}")

            if args.post_step_wait > 0 and not stop_event.is_set():
                time.sleep(args.post_step_wait)

    finally:
        if keyboard_mod and hotkey_handle is not None:
            keyboard_mod.remove_hotkey(hotkey_handle)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
