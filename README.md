# CUA Form-Fill Workflow (Opus 4.5)

This repo now focuses on a single flow: use Claude Opus 4.5 with CUA’s computer interface to extract screen coordinates, run a deterministic macro to fill the DRG Webgrouper form, and (optionally) verify/fix before submitting.

## What’s included
- `flow_utils.py` – reusable helpers: run an LLM task, record raw actions, replay actions.
- `scripts/llm_coord_macro_onepass.py` – one-pass pipeline (builder → macro fill → verify/fix → submit).
- `scripts/llm_macro_builder.py` – builder-only (extract coords JSON).
- `scripts/coord_macro_runner.py` – deterministic filler using coords + payload JSON.
- `scripts/llm_record_run.py` – LLM task runner + macro recorder (saves LLM macro + raw actions).
- `scripts/replay_actions_raw.py` – replay raw computer_call actions (supports wait + masked creds).
- `data/payload.json` – sample input payload.

Legacy example files were removed; `main.py` remains untouched (not used in this flow).

## Prereqs
- Docker desktop running the CUA XFCE image and API on host mode (example):  
  `docker run --rm --name cua-desktop -p 8000:8000 -p 8006:6901 local/cua-xfce:latest`
- noVNC at `http://localhost:8006` (password `password`) for visual check.
- `.env` with:
  - `CUA_PROVIDER=host`
  - `CUA_HOST=127.0.0.1`
  - `CUA_PORT=8000`
  - `CUA_MODEL=anthropic/claude-opus-4-5-20251101`
  - `ANTHROPIC_API_KEY=<your key>` (BYOK)
- Venv activated: `. .venv/bin/activate`
- You may need a slightly higher delay for stability (default 0.35s).

## One-pass run (recommended)
```bash
python scripts/llm_coord_macro_onepass.py \
  --url "https://www.drg-research-group.de/index.php?option=com_webgrouper&view=webgrouper&Itemid=112" \
  --data data/payload.json \
  --coords-out data/coords_generated.json
```
Behavior:
- Builder (LLM, ≤2 screenshots): zooms to ~80%, stabilizes scroll, emits per-cell coords JSON; no filling.
- Macro (CUA): fills using coords + payload, clears each field (Ctrl+A + backspace/delete) before typing. Tab-walk is enabled by default:
  - Click gender, type, Enter to confirm, then Tab through remaining scalar fields.
  - Diagnoses: click first cell, type each diagnosis, Tab between cells.
  - Procedures: click first code cell, type code, Tab, type date, Tab to next row.
- Submit is deferred until verify passes.
- Verify (default 10 steps): read-only check. If mismatches, LLM returns `FIX_ACTIONS`; the script applies fixes and re-verifies once, then submits. Set `--verify-steps 0` to skip verify (no auto-submit).

Key flags:
- `--tab-walk / --no-tab-walk` (default tab-walk on for scalar fields).
- `--delay` (default 0.35).
- `--verify-steps` (default 10; set 0 to skip).

## Builder + manual fill (if you want to separate steps)
1) Builder only:  
   ```bash
   python scripts/llm_macro_builder.py \
     --url "https://www.drg-research-group.de/index.php?option=com_webgrouper&view=webgrouper&Itemid=112" \
     --data data/payload.json \
     --out data/coords_generated.json
   ```
2) Fill with deterministic macro:  
   ```bash
   python scripts/coord_macro_runner.py \
     --data data/payload.json \
     --coords data/coords_generated.json \
     --delay 0.35
   ```
(This path skips verify/submit; run a verifier separately if needed.)

## Field handling details
- Clearing: every field is cleared before typing (Ctrl+A, backspace, delete).
- Dropdowns: typed by default (per your preference). Gender seed confirms with Enter before tab-walk.
- Diagnoses: direct click per cell (no tab-walk).
- Procedures: click code cell, then Tab to the date field (the date field appears after code is filled).
- Submit: only clicked after a clean verify (or verify skipped).

## Troubleshooting tips
- If coords are wrong, rerun the builder (it must keep a stable zoom/scroll).
- If tab order differs, use `--no-tab-walk` to fill scalar fields by per-field coords.
- If entries concatenate, raise `--delay` (e.g., `--delay 0.5`).
- If verification cost is a concern, set `--verify-steps 0` and submit manually.

## Current status
- LLM builds the macro plan (coords/fixes). All clicks/typing are executed via the CUA computer interface.
- Submit is gated on verification success to avoid bad runs. Use flags to tune tab-walk and delays per environment. 

## Orchestrated DRG → Datalytics flow
Use `scripts/flow_orchestrator.py` for the alternating LLM+macro flow across two sites.

Env requirements (datalytics login):
- `DATALYTICS_USER`
- `DATALYTICS_PASS`

Example:
```bash
python scripts/flow_orchestrator.py \
  --drg-url "https://www.drg-research-group.de/index.php?option=com_webgrouper&view=webgrouper&Itemid=112" \
  --drg-data data/payload.json \
  --datalytics-url "https://datalytics.drg-pepp.de/drg-pepp-grouper/grouping" \
  --datalytics-data data/datalytics_payload.json \
  --builder-steps 60 \
  --verify-steps 10 \
  --max-fix-rounds 1 \
  --delay 0.35
```

Behavior:
- DRG: LLM maps coords (if missing), macro fills, LLM verifies → fixes → submit.
- Datalytics: LLM logs in, macro fills, LLM verifies → fixes.

If a verify step finds mismatches, you can optionally recenter the viewport before applying fixes by adding `--recenter-before-fix`.

Datalytics notes:
- The builder now clicks "Diagnose hinzufügen" / "Prozedur hinzufügen" to create enough rows before mapping.
- The macro will click those buttons before filling so all rows exist.

## Modular helpers (for custom flows)
`flow_utils.py` exposes utilities so you can compose your own flows:

- `run_llm_task(task, ...)` → returns `{ "macro": <LLM JSON>, "actions": [computer_call...] }`
- `replay_actions(actions, delay, email, password)` → replays raw actions via CUA
- `render_task(template, {"EMAIL": "...", "PASSWORD": "..."})` → replace placeholders
- `save_json(path, payload)` / `load_json(path)`

Example custom flow:
```python
import asyncio
from flow_utils import render_task, run_llm_task, replay_actions, save_json

TASK = (
  "Click address bar, go to https://www.linkedin.com/login, sign in with <EMAIL>/<PASSWORD>, "
  "then open Jobs. Output [[[MACRO]]] JSON."
)

async def main():
    task = render_task(TASK, {"EMAIL": "you@example.com", "PASSWORD": "•••"})
    result = await run_llm_task(task=task, macro_tag="MACRO", keep_screens=True, mask_text=True)
    save_json("trajectories/macro.json", result.get("macro") or {"actions": []})
    save_json("trajectories/actions.json", {"actions": result.get("actions") or []})
    await replay_actions(result.get("actions") or [], delay=0.5, email="you@example.com", password="•••")

asyncio.run(main())
```

Command-line recorder (no Ctrl+L, click-based URL entry):
```bash
python scripts/llm_record_run.py \
  --email "you@example.com" \
  --password "your-password" \
  --mask-text \
  --keep-screens \
  --out trajectories/recorded_macro.json \
  --actions-out trajectories/recorded_actions.json
```

## Datalytics recorder (LLM-run + macro capture)
Runs a full Datalytics fill with the LLM and records:
1) Raw computer_call actions (comprehensive replay).
2) LLM-emitted macro JSON at the end.

```bash
python scripts/datalytics_record_run.py \
  --data data/datalytics_payload.json \
  --user "$DATALYTICS_USER" \
  --password "$DATALYTICS_PASS" \
  --mask-text \
  --out-llm trajectories/datalytics_macro_llm.json \
  --out-raw trajectories/datalytics_actions_raw.json
```

Replay the LLM macro with payload substitution:
```bash
python scripts/datalytics_macro_replay.py \
  --macro trajectories/datalytics_macro_llm.json \
  --data data/datalytics_payload.json \
  --user "$DATALYTICS_USER" \
  --password "$DATALYTICS_PASS" \
  --auto-clear \
  --delay 0.5
```
