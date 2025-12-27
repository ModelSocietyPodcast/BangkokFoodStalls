# FoodStallSim

Scaffold for a food stall simulation prototype.

## Windows PowerShell setup

Set-Location FoodStallSim
python -m venv .venv
. .\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt

## Run

With the venv active from FoodStallSim root:
$env:PYTHONPATH = "src"
python -m foodstallsim.app
