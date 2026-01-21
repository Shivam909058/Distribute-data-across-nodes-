#!/bin/bash
# ============================================
# VISHWARUPA - TERMUX + PROOT UBUNTU SETUP
# ============================================
# This script sets up Vishwarupa on Android phone
# using Termux with proot-distro (Ubuntu)
#
# Prerequisites: Install Termux from F-Droid
# ============================================

set -e

echo "================================================"
echo "  VISHWARUPA - Termux Setup Script"
echo "================================================"
echo ""

# Detect environment
if [ -d "/data/data/com.termux" ]; then
    echo "✓ Running in Termux environment"
    TERMUX_ENV=1
else
    echo "✓ Running in Linux/Ubuntu environment"
    TERMUX_ENV=0
fi

# ============================================
# STEP 1: Install dependencies
# ============================================
echo ""
echo "[1/5] Installing dependencies..."

if [ "$TERMUX_ENV" = "1" ]; then
    # Termux native
    pkg update -y
    pkg upgrade -y
    pkg install -y rust python python-pip git
else
    # proot Ubuntu or native Linux
    if command -v apt &> /dev/null; then
        sudo apt update
        sudo apt install -y build-essential curl git python3 python3-pip python3-venv
        
        # Install Rust
        if ! command -v rustc &> /dev/null; then
            echo "Installing Rust..."
            curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
            source "$HOME/.cargo/env"
        fi
    fi
fi

echo "✓ Dependencies installed"

# ============================================
# STEP 2: Clone or navigate to project
# ============================================
echo ""
echo "[2/5] Setting up project..."

VISHWARUPA_DIR="$HOME/Vishwarupa"

if [ -d "$VISHWARUPA_DIR" ]; then
    echo "✓ Project directory exists at $VISHWARUPA_DIR"
    cd "$VISHWARUPA_DIR"
else
    echo "Creating project directory..."
    mkdir -p "$VISHWARUPA_DIR"
    cd "$VISHWARUPA_DIR"
    
    # If this script is run from a different location with source files
    if [ -f "./src/main.rs" ]; then
        cp -r ./* "$VISHWARUPA_DIR/"
    fi
fi

# ============================================
# STEP 3: Build Rust agent
# ============================================
echo ""
echo "[3/5] Building Vishwarupa agent..."

if [ -f "Cargo.toml" ] || [ -f "cargo.toml" ]; then
    # Source cargo env if exists
    [ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
    
    echo "This may take 5-10 minutes on a phone..."
    cargo build --release
    echo "✓ Agent built successfully"
else
    echo "⚠ Cargo.toml not found. Please copy the project files first."
    exit 1
fi

# ============================================
# STEP 4: Setup Python environment
# ============================================
echo ""
echo "[4/5] Setting up Python environment..."

if [ "$TERMUX_ENV" = "1" ]; then
    pip install fastapi uvicorn python-multipart aiofiles pydantic
else
    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install -r requirements.txt
fi

echo "✓ Python environment ready"

# ============================================
# STEP 5: Create master key
# ============================================
echo ""
echo "[5/5] Setting up encryption key..."

if [ ! -f "master_9000.key" ]; then
    # Generate a default key for quick setup
    # In production, this should be the same as other devices!
    echo ""
    echo "⚠ IMPORTANT: For devices to work together, they must use the SAME master password!"
    echo ""
    echo "Do you want to:"
    echo "  1) Enter master password (same as laptop)"
    echo "  2) Generate new random key (for testing only)"
    read -p "Choice [1/2]: " choice
    
    if [ "$choice" = "1" ]; then
        echo "Starting agent to enter password..."
        ./target/release/vishwarupa id &
        sleep 2
        kill %1 2>/dev/null || true
    else
        echo "Generating test key..."
        head -c 32 /dev/urandom | xxd -p | tr -d '\n' > master_9000.key
        echo "✓ Test key generated (master_9000.key)"
    fi
else
    echo "✓ Master key already exists"
fi

# ============================================
# DONE - Print instructions
# ============================================
echo ""
echo "================================================"
echo "  SETUP COMPLETE!"
echo "================================================"
echo ""
echo "To start Vishwarupa on this phone:"
echo ""
echo "  ./start_phone.sh"
echo ""
echo "Then open browser on your phone:"
echo ""
echo "  http://localhost:8000"
echo ""
echo "To connect with laptop:"
echo "  1. Start server on laptop: python server.py"
echo "  2. Set SERVER_URL: export SERVER_URL=http://<laptop-ip>:8000"
echo "  3. Start agent: ./target/release/vishwarupa"
echo ""
echo "================================================"
