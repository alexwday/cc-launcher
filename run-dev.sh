#!/bin/bash
# Run CC-Launcher in development mode (placeholder responses, no SSL)

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
pip install -q Flask flask-cors requests python-dotenv 2>/dev/null

# Run with dev settings
export DEV_MODE=true
export USE_PLACEHOLDER_MODE=true
export SKIP_SSL_VERIFY=true
export AUTO_OPEN_BROWSER=true

echo "Starting CC-Launcher in development mode..."
python app.py
