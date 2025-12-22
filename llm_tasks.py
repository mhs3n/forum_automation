"""Prompt builders for LLM-driven CUA tasks."""

from __future__ import annotations


def build_open_sheet_task(url: str, sheet_name: str) -> str:
    return (
        "You are controlling Chrome. Steps:\n"
        f"1) Go to {url}\n"
        "2) If a list of recent sheets appears, locate the sheet named "
        f"'{sheet_name}' and open it.\n"
        "3) If a search box is available, use it to find the sheet by name.\n"
        "4) Wait until the sheet grid is visible and stable.\n"
        "Use clicks/typing (no keyboard shortcuts). Do not fill any cells.\n"
        "When the sheet is open, respond with the word DONE."
    )


def build_fill_task(diagnoses: list[str], procedures: list[str], procedure_dates: list[str]) -> str:
    return (
        "You are on the DRG form. Fill ONLY the diagnoses and procedures fields.\n"
        "Do not change other fields. Use clicks and typing, no keyboard shortcuts.\n"
        "Diagnoses (in order):\n"
        f"{diagnoses}\n"
        "Procedures with dates (in order):\n"
        f"{list(zip(procedures, procedure_dates))}\n"
        "When finished, respond with DONE."
    )
