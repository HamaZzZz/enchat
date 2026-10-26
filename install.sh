#!/usr/bin/env bash
set -e

echo "=== Installing EnChat ==="

# Create virtual environment if not exists
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# Activate venv and install dependencies
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create local bin directory if missing
mkdir -p ~/.local/bin

# Create wrapper script
cat << 'EOF' > ~/.local/bin/enchat
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Locate repository root relative to script or installation path
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")/enchat" # adjust as appropriate
# Or run directly via absolute path:
source "$(dirname "$(realpath "$0")")/../.venv/bin/activate" 2>/dev/null || true
# Fallback execution:
python3 -m enchat
EOF

# Make wrapper executable
chmod +x ~/.local/bin/enchat

echo "=== Installation Complete! ==="
echo "Ensure ~/.local/bin is in your PATH, then type 'enchat' to launch."
