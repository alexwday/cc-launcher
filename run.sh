#!/bin/bash
# Run CC-Launcher in production mode

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check for virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies (skip rbc_security errors)
echo "Installing dependencies..."
pip install -q Flask flask-cors requests python-dotenv 2>/dev/null
pip install -q rbc_security 2>/dev/null || echo "Note: rbc_security not available (expected outside RBC environment)"

# Check for .env file
if [ ! -f ".env" ]; then
    echo "No .env file found. Copying from .env.example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Please edit .env with your configuration, then run again."
        exit 1
    fi
fi

# Run the application
python app.py
