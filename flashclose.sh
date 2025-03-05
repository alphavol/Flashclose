#!/bin/bash

if [ ! -d ".venv" ]; then
    echo "Error: Virtual environment '.venv' not found!"
    echo "Please run 'python -m venv .venv' and 'pip install -r requirements.txt' first."
    exit 1
fi

if [ ! -f "run.py" ]; then
    echo "Error: run.py not found in current directory!"
    echo "Please make sure you're running this script from the project root directory."
    exit 1
fi

source .venv/bin/activate

python3 run.py

deactivate
