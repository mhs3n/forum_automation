# CUA + PMC Automation Toolkit

This repo provides building blocks to alternate between:
- CUA LLM steps (visual navigation in a desktop UI)
- PMC macros (deterministic clicks/typing recorded on Windows)

The included example flow:
1) LLM opens a Google Sheet in the CUA desktop
2) PMC runs one or two macros to fill content
3) Optional LLM step to fill diagnoses/procedures

## Repo structure
- `flow_utils.py`: LLM run helper + raw action replay
- `pmc_utils.py`: PMC launcher helpers (autoplay, kill, wait)
- `llm_tasks.py`: prompt builders for LLM tasks
- `scripts/llm_open_sheet_then_pmc.py`: example flow (LLM -> PMC -> PMC -> LLM)
- `scripts/llm_record_run.py`: record LLM actions to JSON
- `scripts/replay_actions_raw.py`: replay recorded LLM actions
- `utils.py`: minimal `.env` loader

## Prereqs
- Python 3.12 in a venv
- Docker Desktop for the CUA desktop (Linux XFCE)
- Pulover's Macro Creator (PMC) on Windows
- `.env` or `.env.local` with your API key

## Setup
Create venv and install dependencies:
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\requirements.txt
python -m pip install .\cua_som-0.1.3-py3-none-any.whl
```

Set env :
```
CUA_PROVIDER=host
CUA_HOST=127.0.0.1
CUA_PORT=8000
CUA_MODEL=anthropic/claude-opus-4-5-20251101
ANTHROPIC_API_KEY=your_key_here
```

## CUA desktop (Docker)
Pull and run the desktop:
```powershell
docker pull --platform=linux/amd64 trycua/cua-xfce:latest
docker run --rm --name cua-desktop -p 8000:8000 -p 8006:6901 trycua/cua-xfce:latest
```

Open noVNC: `http://localhost:8006` (password `password`)

## PMC tips (Windows)
- If you want PMC to drive the CUA desktop, record the macro against the noVNC
  window (click inside the browser canvas).
- Keep window size and scale fixed (Windows scaling 100%, browser zoom 100%).
- Autoplay flags can be ignored if PMC is already open. Use `--pmc-kill`.

## Example flow (LLM -> PMC -> PMC -> LLM)
This opens a sheet, runs two macros, then fills diagnoses/procedures.

```powershell
.\venv\Scripts\python .\scripts\llm_open_sheet_then_pmc.py ^
  --pmc-file "C:\Users\souli\AppData\Roaming\MacroCreator\sheets_split.pmc" ^
  --macro-index 1 ^
  --macro-index-2 2 ^
  --post-open-wait 5 ^
  --post-macro-wait 2 ^
  --pmc-silent ^
  --pmc-wait ^
  --pmc-kill ^
  --fill-after-macros ^
  --verbose
```

Key flags:
- `--pmc-silent`: use `-sN` (autoplay + hide + close)
- `--pmc-wait`: wait until PMC exits
- `--pmc-kill`: close any open PMC before launching (there is an issue where pmc wont work if the app is open)
- `--fill-after-macros`: run the second LLM fill step

## PMC -> LLM Wordle test (host Windows)
Record a PMC macro that navigates to Wordle, then let the LLM solve it:
```powershell
.\venv\Scripts\python .\scripts\pmc_wordle_then_llm.py ^
  --pmc-file "C:\Users\Administrator\Desktop\wordle.pmc" ^
  --pmc-index 1 ^
  --pmc-wait ^
  --pmc-kill
```
Press `F12` to stop everything.

### Wordle test quickstart
Prereqs:
- Python venv with dependencies installed.
- CUA computer server running on this Windows host.
- Pulover's Macro Creator (PMC) with a macro that opens Wordle.

Install deps (includes the F12 hotkey support):
```powershell
.\venv\Scripts\pip.exe install -r .\requirements.txt
```

Start the host computer server:
```powershell
.\venv\Scripts\python -m computer_server.main --host 127.0.0.1 --port 8000 --os windows
```

Run the flow:
```powershell
.\venv\Scripts\python .\scripts\pmc_wordle_then_llm.py ^
  --pmc-file "C:\Users\Administrator\Desktop\wordle.pmc" ^
  --pmc-index 1 ^
  --pmc-wait ^
  --pmc-kill ^
  --log-actions
```

Notes:
- Press `F12` to stop the flow and regain control.
- The LLM types full guesses and presses Enter (no on-screen tile clicking).
- Screen resolution and scaling affect CUA reliability. If clicks look off or are slow,
  reduce resolution, keep Windows scaling at 100%, and avoid resizing the target window.

## Building blocks for custom flows
Use `flow_utils.run_llm_task` for LLM steps and `pmc_utils.run_pmc` for macros.

Example pattern:
1) `run_llm_task` to navigate
2) `run_pmc` to replay a macro
3) `run_llm_task` to verify/fix
4) Repeat as needed

### Crafting your own flow
Use `scripts/pmc_wordle_then_llm.py` as a template. It supports:
- `--steps pmc,llm,pmc,llm` to alternate between PMC and LLM steps.
- `--pmc-indices 1,2` to run different macros for each PMC step.
- `--log-actions` and `--log-messages` to see what the LLM is doing.

To build a new flow:
1) Create your PMC macros for deterministic navigation or form filling.
2) Add a new LLM task prompt in `llm_tasks.py`.
3) Wire the steps in a new script under `scripts/`, calling `run_llm_task` and `run_pmc_step`.
4) Keep screen resolution and scaling stable to maximize CUA accuracy.

## Recording and replaying LLM actions (Ignore this because it was done before integrating pmc)
Record:
```powershell
.\venv\Scripts\python .\scripts\llm_record_run.py --task "Open site and log in"
```

Replay:
```powershell
.\venv\Scripts\python .\scripts\replay_actions_raw.py --actions trajectories\recorded_actions.json
```

## Troubleshooting
- "Computer API Server connection lost": the CUA Docker container is not running.
- PMC opens but macro does not run: confirm macro index and use `--pmc-kill` + `--pmc-silent`.

## Working on adaptations
For testing and adaptation, create a sub-branch from this branch and work there.
