# CUA Form-Fill Workflow (Opus 4.5)

This repo now focuses on a single flow: use Claude Opus 4.5 with CUA’s computer interface to extract screen coordinates, run a deterministic macro to fill the DRG Webgrouper form, and (optionally) verify/fix before submitting.

## What’s included
- `scripts/llm_coord_macro_onepass.py` – one-pass pipeline (builder → macro fill → verify/fix → submit).
- `scripts/llm_macro_builder.py` – builder-only (extract coords JSON).
- `scripts/coord_macro_runner.py` – deterministic filler using coords + payload JSON.
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
- Diagnoses/procedures/dates: tab-walked cell-by-cell; requires correct coords for the first cells.
- Submit: only clicked after a clean verify (or verify skipped).

## Troubleshooting tips
- If coords are wrong, rerun the builder (it must keep a stable zoom/scroll).
- If tab order differs, use `--no-tab-walk` to fill scalar fields by per-field coords.
- If entries concatenate, raise `--delay` (e.g., `--delay 0.5`).
- If verification cost is a concern, set `--verify-steps 0` and submit manually.

## Current status
- LLM builds the macro plan (coords/fixes). All clicks/typing are executed via the CUA computer interface.
- Submit is gated on verification success to avoid bad runs. Use flags to tune tab-walk and delays per environment. 
