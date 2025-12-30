# AGENTS.md

## Overview
BangkokFoodStalls contains a Python food stall simulation prototype under `FoodStallSim`.

## Quickstart (Windows PowerShell)
Set-Location FoodStallSim
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

With the venv active from `FoodStallSim` root:
$env:PYTHONPATH = "src"
python -m foodstallsim.app

## Tests
No automated tests are currently defined. The `FoodStallSim/tests` package is a placeholder for future tests.

## Repo structure
- `FoodStallSim/src`: Python source for the simulation.
- `FoodStallSim/tests`: Placeholder package for future automated tests.
- `FoodStallSim/assets`: Art/audio assets used by the simulation.
- `FoodStallSim/docs`: Project documentation.

## Coding conventions
- Prefer standard library and minimal dependencies.
- Use `snake_case` for functions/variables and `PascalCase` for classes.
- Keep functions small and focused; avoid deep nesting.
- Add concise comments only where logic is non-obvious.

## Assets
- Add new assets under the appropriate `FoodStallSim/assets` subfolder.
- Keep filenames descriptive and lowercase where practical.

## Git hygiene
- Do not commit local virtual environments or cache files.
- Keep large binaries to a minimum; confirm before adding sizable assets.
