# CUA on Host Machine (Windows)

This guide documents how to run CUA on the local Windows host, how to run the PMC -> LLM Wordle test, and how to craft your own PMC/LLM flows.


Install dependencies:
```powershell
.\venv\Scripts\pip.exe install -r .\requirements.txt
```

## Start the host computer server
```powershell
.\venv\Scripts\python -m computer_server.main --host 127.0.0.1 --port 8000 --os windows
```

## Wordle test (PMC -> LLM) on this machine
Record a PMC macro that opens Wordle and save it to the Desktop as `wordle.pmc`, then run:
```powershell
.\venv\Scripts\python .\scripts\pmc_wordle_then_llm.py ^
  --pmc-file "C:\Users\Administrator\Desktop\wordle.pmc" ^
  --pmc-index 1 ^
  --pmc-wait ^
  --pmc-kill ^
  --log-actions
```

Hotkey:
- Press `F12` to stop everything and regain control.

Notes:
- The LLM types full guesses and presses Enter (no on-screen tile clicking).
- Screen resolution and scaling affect CUA reliability. Keep scaling at 100% and avoid resizing windows mid-run.

## Crafting your own flow
Use `scripts/pmc_wordle_then_llm.py` as a template.

Key ideas:
1) Record deterministic navigation or setup steps with PMC.
2) Create a matching LLM task prompt in `llm_tasks.py`.
3) Chain PMC and LLM steps using `--steps` and `--pmc-indices`.

Example (PMC -> LLM -> PMC):
```powershell
.\venv\Scripts\python .\scripts\pmc_wordle_then_llm.py ^
  --steps "pmc,llm,pmc" ^
  --pmc-file "C:\Users\Administrator\Desktop\your_flow.pmc" ^
  --pmc-indices "1,2" ^
  --pmc-wait ^
  --pmc-kill ^
  --log-actions ^
  --log-messages
```

## Troubleshooting
- Hotkey does not work: install the `keyboard` package and run the terminal as Administrator.
- LLM clicks the wrong spots: lower resolution, keep scaling at 100%, and keep the target window size fixed.
- PMC fails to run: verify macro index, and use `--pmc-kill`.
