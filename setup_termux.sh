#!/bin/bash
# ============================================
# VISHWARUPA - UBUNTU / PROOT-UBUNTU SETUP
# ============================================
# Works in:
# - proot Ubuntu (Termux)
# - Native Ubuntu
# - WSL / VM / Cloud Linux
#
# Run with:
#   bash setup_ubuntu.sh
# ============================================

set -e

echo "================================================"
echo "  VISHWARUPA - Ubuntu Setup Script"
echo "================================================"
echo ""

# --------------------------------------------
# Sanity checks
# --------------------------------------------
if ! command -v apt >/dev/null 2>&1; then
    echo "❌ This script requires Ubuntu/Debian (apt not found)"
    exit 1
fi

echo "✓ Ubuntu/Debian environment detected"

# --------------------------------------------
# STEP 1: Install system dependencies
# --------------------------------------------
echo ""
echo "[1/5] Installing system dependencies..."

apt update
apt install -y \
    build-essential \
    curl \
    git \
    ca-certificates \
    pkg-config \
    python3 \
    python3-pip \
    python3-venv

echo "✓ System dependencies installed"

# --------------------------------------------
# STEP 2: Install Rust (if missing)
# --------------------------------------------
echo ""
echo "[2/5] Checking Rust installation..."

if ! command -v rustc >/dev/null 2>&1; then
    echo "Rust not found, installing..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y
    source "$HOME/.cargo/env"
else
    echo "✓ Rust already installed"
    source "$HOME/.cargo/env" || true
fi

# --------------------------------------------
# STEP 3: Setup project directory
# --------------------------------------------
echo ""
echo "[3/5] Setting up project directory..."

# Use the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check if we're already in a project directory (has Cargo.toml)
if [ -f "$SCRIPT_DIR/Cargo.toml" ]; then
    VISHWARUPA_DIR="$SCRIPT_DIR"
    echo "✓ Running from project directory: $VISHWARUPA_DIR"
else
    # Fallback to home directory
    VISHWARUPA_DIR="$HOME/Vishwarupa"
    if [ ! -d "$VISHWARUPA_DIR" ]; then
        echo "Creating $VISHWARUPA_DIR"
        mkdir -p "$VISHWARUPA_DIR"
    fi
    
    # Copy files if script is in a different location with Cargo.toml
    if [ "$SCRIPT_DIR" != "$VISHWARUPA_DIR" ] && [ -f "$SCRIPT_DIR/../Cargo.toml" ]; then
        echo "Copying project files..."
        cp -r "$SCRIPT_DIR/../"* "$VISHWARUPA_DIR/"
    fi
fi

cd "$VISHWARUPA_DIR"
echo "📂 Working in: $(pwd)"

# --------------------------------------------
# STEP 4: Build Rust agent
# --------------------------------------------
echo ""
echo "[4/5] Building Vishwarupa agent..."

if [ ! -f "Cargo.toml" ]; then
    echo "❌ Cargo.toml not found in $VISHWARUPA_DIR"
    echo "👉 Clone or copy the repository here first"
    exit 1
fi

echo "This may take several minutes..."
cargo build --release

echo "✓ Rust agent built successfully"

# --------------------------------------------
# STEP 5: Setup Python environment
# --------------------------------------------
echo ""
echo "[5/5] Setting up Python environment..."

# Find Python 3 command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "❌ Python not found!"
    exit 1
fi

echo "Using: $PYTHON_CMD"

if [ ! -d "venv" ]; then
    $PYTHON_CMD -m venv venv
fi

source venv/bin/activate

if [ -f "requirements.txt" ]; then
    pip install --upgrade pip
    pip install -r requirements.txt
else
    pip install \
        fastapi \
        uvicorn \
        python-multipart \
        aiofiles \
        pydantic
fi

echo "✓ Python environment ready"

# --------------------------------------------
# STEP 6: Setup master encryption key
# --------------------------------------------
echo ""
echo "[6/6] Setting up encryption key..."

KEY_FILE="master_9000.key"

if [ ! -f "$KEY_FILE" ]; then
    echo ""
    echo "⚠ IMPORTANT"
    echo "All nodes must use the SAME master key to communicate."
    echo ""
    echo "Choose an option:"
    echo "  1) Enter master password manually"
    echo "  2) Generate random test key (NOT for production)"
    read -rp "Choice [1/2]: " choice

    if [ "$choice" = "1" ]; then
        read -rsp "Enter master password: " password
        echo ""
        echo -n "$password" | sha256sum | cut -d' ' -f1 > "$KEY_FILE"
        echo "✓ Master key created"
    else
        head -c 32 /dev/urandom | xxd -p | tr -d '\n' > "$KEY_FILE"
        echo "✓ Test key generated at $KEY_FILE"
    fi
else
    echo "✓ Master key already exists"
fi

# --------------------------------------------
# DONE
# --------------------------------------------
echo ""
echo "================================================"
echo "  SETUP COMPLETE 🎉"
echo "================================================"
echo ""
echo "To start the agent:"
echo ""
echo "  source venv/bin/activate"
echo "  ./target/release/vishwarupa"
echo ""
echo "To start the server:"
echo ""
echo "  source venv/bin/activate"
echo "  python3 server.py"
echo ""
echo "Or use the helper script:"
echo "  bash start_phone.sh"
echo ""
echo "================================================"
