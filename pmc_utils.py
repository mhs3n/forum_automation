"""PMC helpers for launching macros via MacroCreator.exe."""

from __future__ import annotations

import os
import subprocess
from typing import Optional


def resolve_pmc_exe(pmc_exe: Optional[str]) -> str:
    if pmc_exe and os.path.exists(pmc_exe):
        return pmc_exe

    env_exe = os.getenv("PMC_EXE")
    if env_exe and os.path.exists(env_exe):
        return env_exe

    candidates = [
        r"C:\Program Files\MacroCreator\MacroCreator.exe",
        r"C:\Program Files\Pulover's Macro Creator\MacroCreator.exe",
        r"C:\Program Files (x86)\MacroCreator\MacroCreator.exe",
        r"C:\Program Files (x86)\Pulover's Macro Creator\MacroCreator.exe",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    raise FileNotFoundError("MacroCreator.exe not found. Pass --pmc-exe or set PMC_EXE.")


def resolve_pmc_file(pmc_file: Optional[str]) -> str:
    if pmc_file and os.path.exists(pmc_file):
        return pmc_file
    env_file = os.getenv("PMC_FILE")
    if env_file and os.path.exists(env_file):
        return env_file
    raise FileNotFoundError("PMC file not found. Pass --pmc-file or set PMC_FILE.")


def run_pmc(
    pmc_exe: str,
    pmc_file: str,
    macro_index: int,
    close_after: bool,
    wait: bool,
    kill_existing: bool,
    silent: bool,
) -> None:
    if kill_existing:
        subprocess.run(["taskkill", "/IM", "MacroCreator.exe", "/F"], check=False)

    flag = f"-s{macro_index}" if silent else f"-a{macro_index}"
    args = [pmc_exe, pmc_file, flag]
    if close_after and not silent:
        args.append("-c")
    print(f"[pmc] launch: {args}")
    proc = subprocess.Popen(args)
    print(f"[pmc] pid {proc.pid}")
    if wait:
        proc.wait()
