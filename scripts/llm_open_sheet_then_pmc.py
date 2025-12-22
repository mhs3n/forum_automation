"""Open a Google Sheet with CUA LLM, then run a PMC macro to fill it."""

import argparse
import asyncio
import os
import sys
from getpass import getpass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flow_utils import run_llm_task
from llm_tasks import build_fill_task, build_open_sheet_task
from pmc_utils import resolve_pmc_exe, resolve_pmc_file, run_pmc
from utils import load_dotenv_files


DEFAULT_URL = "https://docs.google.com/spreadsheets/u/0/?ec=wgc-sheets-hero-goto"
DEFAULT_DIAGNOSES = ["I80.28", "M62.89", "I10.00", "E78.5", "E79.0", "E04.2"]
DEFAULT_PROCEDURES = ["3-035:l", "3-806:l"]
DEFAULT_PROCEDURE_DATES = ["22.11.2025", "24.11.2025"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Use CUA to open a sheet, then run a PMC macro.")
    p.add_argument("--url", default=DEFAULT_URL, help="Google Sheets landing URL.")
    p.add_argument("--sheet-name", default="test for automation", help="Sheet name to open.")
    p.add_argument("--pmc-file", default=None, help="Path to .pmc file.")
    p.add_argument("--pmc-exe", default=None, help="Path to MacroCreator.exe.")
    p.add_argument("--macro-index", type=int, default=2, help="PMC macro index to run (1-based).")
    p.add_argument("--max-steps", type=int, default=120, help="Max LLM steps (default 120).")
    p.add_argument("--post-open-wait", type=float, default=2.0, help="Seconds to wait before running PMC.")
    p.add_argument("--no-close", action="store_true", help="Do not close PMC after macro runs.")
    p.add_argument("--pmc-wait", action="store_true", help="Wait for PMC to exit before continuing.")
    p.add_argument("--pmc-kill", action="store_true", help="Kill existing MacroCreator.exe before launch.")
    p.add_argument("--pmc-silent", action="store_true", help="Use PMC silent mode (-sN) instead of -aN.")
    p.add_argument("--macro-index-2", type=int, default=None, help="Optional second PMC macro index to run after the first.")
    p.add_argument("--post-macro-wait", type=float, default=2.0, help="Seconds to wait between macros.")
    p.add_argument("--fill-after-macros", action="store_true", help="Run a second LLM task to fill diagnoses/procedures.")
    p.add_argument("--diagnoses", default=",".join(DEFAULT_DIAGNOSES), help="Comma-separated diagnosis codes.")
    p.add_argument("--procedures", default=",".join(DEFAULT_PROCEDURES), help="Comma-separated procedure codes.")
    p.add_argument("--procedure-dates", default=",".join(DEFAULT_PROCEDURE_DATES), help="Comma-separated procedure dates.")
    p.add_argument("--anthropic-key", default=None, help="Anthropic API key (overrides env).")
    p.add_argument("--prompt-key", action="store_true", help="Prompt for Anthropic API key.")
    p.add_argument("--verbose", action="store_true", help="Print LLM step progress.")
    return p.parse_args()


async def open_sheet(url: str, sheet_name: str, max_steps: int, verbose: bool) -> None:
    task = build_open_sheet_task(url, sheet_name)
    print("[flow] LLM opening sheet...")
    result = await run_llm_task(task=task, max_steps=max_steps, macro_tag=None, verbose=verbose)
    if result is None:
        raise RuntimeError("LLM task did not return a result.")
    print("[flow] LLM done. Sheet should be open.")


async def fill_form(
    diagnoses: list[str],
    procedures: list[str],
    procedure_dates: list[str],
    max_steps: int,
    verbose: bool,
) -> None:
    task = build_fill_task(diagnoses, procedures, procedure_dates)
    print("[flow] LLM filling diagnoses/procedures...")
    result = await run_llm_task(task=task, max_steps=max_steps, macro_tag=None, verbose=verbose)
    if result is None:
        raise RuntimeError("LLM fill task did not return a result.")
    print("[flow] LLM fill done.")


def main() -> int:
    args = parse_args()
    load_dotenv_files()
    if args.anthropic_key:
        os.environ["ANTHROPIC_API_KEY"] = args.anthropic_key
    elif args.prompt_key and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = getpass("ANTHROPIC_API_KEY: ")
    pmc_exe = resolve_pmc_exe(args.pmc_exe)
    pmc_file = resolve_pmc_file(args.pmc_file)

    asyncio.run(open_sheet(args.url, args.sheet_name, args.max_steps, args.verbose))
    if args.post_open_wait > 0:
        asyncio.run(asyncio.sleep(args.post_open_wait))
        print(f"[flow] waited {args.post_open_wait:.1f}s before PMC.")
    run_pmc(
        pmc_exe,
        pmc_file,
        args.macro_index,
        close_after=not args.no_close,
        wait=args.pmc_wait,
        kill_existing=args.pmc_kill,
        silent=args.pmc_silent,
    )
    print("[flow] PMC launched.")
    if args.macro_index_2 is not None:
        if args.post_macro_wait > 0:
            asyncio.run(asyncio.sleep(args.post_macro_wait))
            print(f"[flow] waited {args.post_macro_wait:.1f}s before PMC macro 2.")
        run_pmc(
            pmc_exe,
            pmc_file,
            args.macro_index_2,
            close_after=not args.no_close,
            wait=args.pmc_wait,
            kill_existing=args.pmc_kill,
            silent=args.pmc_silent,
        )
        print("[flow] PMC macro 2 launched.")
    if args.fill_after_macros:
        diagnoses = [d.strip() for d in args.diagnoses.split(",") if d.strip()]
        procedures = [p.strip() for p in args.procedures.split(",") if p.strip()]
        proc_dates = [d.strip() for d in args.procedure_dates.split(",") if d.strip()]
        if len(procedures) != len(proc_dates):
            raise ValueError("procedures and procedure-dates counts must match.")
        asyncio.run(fill_form(diagnoses, procedures, proc_dates, args.max_steps, args.verbose))
    print("[done] sheet opened and PMC macro started.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
