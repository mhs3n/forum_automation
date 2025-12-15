"""Builder: have the LLM inspect the form (<=3 screenshots) and emit coords JSON for a macro.

Flow:
- Agent opens the target URL.
- Constraint: use at most 3 screenshots.
- Agent should NOT fill the form. It should only map JSON keys to screen coordinates.
- Agent outputs a coords JSON in the format:
  {
    "fields": [
      {"key": "age", "x": 100, "y": 200},
      ...
    ],
    "submit": {"x": 500, "y": 700}
  }
- The agent must wrap the JSON in markers:
  [[[COORDS_JSON]]]
  {...}
  [[[END]]]

This script extracts that JSON and saves it (default: data/coords_generated.json).

Usage:
  python scripts/llm_macro_builder.py \
    --url https://example.com \
    --data data/payload.json \
    --out data/coords_generated.json
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

from agent import ComputerAgent
from computer import Computer
from utils import load_dotenv_files


COORDS_START = r"\[\[\[COORDS_JSON\]\]\]"
COORDS_END = r"\[\[\[END\]\]\]"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM builder: extract coords JSON (<=3 screenshots).")
    p.add_argument("--url", required=True, help="Target URL to inspect.")
    p.add_argument("--data", required=True, help="Path to JSON payload with form keys/values.")
    p.add_argument("--out", default="data/coords_generated.json", help="Where to save coords JSON.")
    p.add_argument("--max-steps", type=int, default=50, help="Max agent steps (default 50).")
    return p.parse_args()


def load_payload(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_messages(url: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "You are inspecting a form to build a coordinate-based macro. Rules:\n"
                "- Use at most 3 screenshots total.\n"
                "- Do NOT fill the form.\n"
                "- Open the page, find each field for the provided JSON keys, and produce coords.\n"
                "- Output only a coords JSON wrapped with markers [[[COORDS_JSON]]] ... [[[END]]].\n"
                "- JSON format:\n"
                "  {\n"
                '    "fields": [ {"key": "<json_key>", "x": <int>, "y": <int>}, ... ],\n'
                '    "submit": {"x": <int>, "y": <int>}  // optional\n'
                "  }\n"
                "- Do not invent keys; use only these:\n"
                f"{list(payload.keys())}\n"
                "- Be concise; no extra text outside the JSON markers.\n\n"
                f"Target URL: {url}\n\n"
                f"Form data (for keys only):\n{json.dumps(payload, indent=2)}"
            ),
        }
    ]


def extract_coords_json(text: str) -> Optional[Dict[str, Any]]:
    pattern = rf"{COORDS_START}(.*?){COORDS_END}"
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        return None
    block = m.group(1).strip()
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        return None


async def run_agent(url: str, payload: Dict[str, Any], max_steps: int) -> Dict[str, Any]:
    load_dotenv_files()

    model = os.getenv("CUA_MODEL", "anthropic/claude-opus-4-5-20251101")
    host = os.getenv("CUA_HOST", "127.0.0.1")
    port = int(os.getenv("CUA_PORT", "8000"))

    computer = Computer(os_type="linux", use_host_computer_server=True, host=host, port=port)
    agent = ComputerAgent(
        model=model,
        tools=[computer],
        only_n_most_recent_images=3,
        verbosity=logging.INFO,
        max_trajectory_budget=10.0,
    )

    messages = build_messages(url, payload)
    steps = 0
    coords: Optional[Dict[str, Any]] = None

    async for result in agent.run(messages, stream=False):
        steps += 1
        for item in result.get("output", []):
            if item.get("type") == "message":
                content = item.get("content", [])
                for part in content:
                    txt = part.get("text")
                    if txt:
                        print(f"[agent] {txt}")
                        maybe = extract_coords_json(txt)
                        if maybe:
                            coords = maybe
        if coords is not None:
            print("[runner] extracted coords JSON.")
            break
        if steps >= max_steps:
            print(f"[runner] Reached max steps ({max_steps}) without coords; stopping.")
            break

    if coords is None:
        raise RuntimeError("Failed to extract coords JSON from agent output.")
    return coords


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    payload = load_payload(args.data)

    coords = asyncio.run(run_agent(args.url, payload, args.max_steps))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(coords, f, indent=2)
    print(f"[runner] saved coords to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
