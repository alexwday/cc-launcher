#!/bin/bash
# Run CC-Launcher

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check for virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q -r src/requirements.txt
pip install -q rbc_security 2>/dev/null || true  # Optional: only available in RBC environment

# Check for .env file
if [ ! -f "src/.env" ]; then
    echo "No .env file found. Copying from .env.example..."
    if [ -f "src/.env.example" ]; then
        cp src/.env.example src/.env
        echo "Please edit src/.env with your configuration, then run again."
        exit 1
    fi
fi

# Run the application
python src/app.py
