#!/bin/bash
# ============================================
# VISHWARUPA - UNIVERSAL START SCRIPT
# ============================================
# Works on: Linux, Android (Termux), macOS, WSL
# Same script for: laptop, phone, tablet, server
# ============================================

set -e

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║           🌐 VISHWARUPA - Distributed Storage             ║"
echo "║                                                           ║"
echo "║   Works on: Phone, Laptop, Tablet, Server - Any Device!  ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"
echo "📂 Working directory: $SCRIPT_DIR"

# Activate venv if exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# ============================================
# DETECT LOCAL IP
# ============================================
get_local_ip() {
    # Try multiple methods for different platforms
    ip route get 1 2>/dev/null | awk '{print $7; exit}' || \
    hostname -I 2>/dev/null | awk '{print $1}' || \
    ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | sed 's/addr://' || \
    ip addr show 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | cut -d/ -f1 || \
    echo ""
}

LOCAL_IP=$(get_local_ip)

if [ -z "$LOCAL_IP" ] || [ "$LOCAL_IP" = "127.0.0.1" ]; then
    echo "⚠ Could not detect your IP address automatically."
    echo "  Check your Wi-Fi/network settings for the IP."
    echo ""
    read -p "Enter this device's IP address: " LOCAL_IP
fi

echo "✓ This device's IP: $LOCAL_IP"
echo ""

# ============================================
# CHOOSE MODE
# ============================================
echo "═══════════════════════════════════════════════════════════"
echo "  Choose how to start:"
echo ""
echo "  [1] START NEW NETWORK (First device / Hub)"
echo "      - This device will host the web UI"
echo "      - Other devices will connect to this one"
echo ""
echo "  [2] JOIN EXISTING NETWORK"
echo "      - Connect to another device already running"
echo "      - You'll need that device's IP address"
echo ""
echo "═══════════════════════════════════════════════════════════"
read -p "Enter choice (1 or 2): " MODE_CHOICE

if [ "$MODE_CHOICE" = "1" ]; then
    # ============================================
    # MODE 1: START NEW NETWORK (HUB)
    # ============================================
    echo ""
    echo "Starting as NETWORK HUB..."
    export LOCAL_IP
    export SERVER_URL="http://127.0.0.1:8000"
    export LISTEN_PORT=9000
    
    # Find Python
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        echo "❌ Python not found! Install: apt install python3 python3-pip"
        exit 1
    fi
    
    # Check dependencies
    if ! $PYTHON_CMD -c "import fastapi" 2>/dev/null; then
        echo "Installing Python dependencies..."
        pip3 install fastapi uvicorn aiofiles python-multipart 2>/dev/null || \
        pip install fastapi uvicorn aiofiles python-multipart
    fi
    
    # Find agent binary
    if [ -f "./target/release/vishwarupa" ]; then
        AGENT="./target/release/vishwarupa"
    elif [ -f "./vishwarupa" ]; then
        AGENT="./vishwarupa"
    else
        echo "Building agent (first time only, may take a few minutes)..."
        if command -v cargo &> /dev/null; then
            cargo build --release
            AGENT="./target/release/vishwarupa"
        else
            echo "❌ Rust not installed! Install:"
            echo "   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
            exit 1
        fi
    fi
    
    echo ""
    echo "Starting web server..."
    $PYTHON_CMD server.py &
    SERVER_PID=$!
    
    # Wait for server
    for i in {1..15}; do
        if curl -s http://127.0.0.1:8000/ > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    
    echo "Starting agent..."
    $AGENT &
    AGENT_PID=$!
    
    sleep 2
    
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║              ✓ VISHWARUPA IS RUNNING                      ║"
    echo "╠═══════════════════════════════════════════════════════════╣"
    echo "║                                                           ║"
    echo "║  📱 Open this URL on ANY device:                          ║"
    echo "║                                                           ║"
    echo "║     👉  http://${LOCAL_IP}:8000                           "
    echo "║                                                           ║"
    echo "║  📋 To add more devices, run on each device:              ║"
    echo "║     ./start.sh  → Choose [2] → Enter: ${LOCAL_IP}         "
    echo "║                                                           ║"
    echo "║  🎬 Features: Upload, Download, Stream Video, Share       ║"
    echo "║                                                           ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
    echo "Press Ctrl+C to stop..."
    
    cleanup() {
        echo ""
        echo "Stopping Vishwarupa..."
        kill $AGENT_PID 2>/dev/null || true
        kill $SERVER_PID 2>/dev/null || true
        exit 0
    }
    trap cleanup SIGINT SIGTERM
    wait

else
    # ============================================
    # MODE 2: JOIN EXISTING NETWORK
    # ============================================
    echo ""
    echo "Enter the IP address of the device running Vishwarupa:"
    echo "(The device that started with option [1])"
    read -p "Hub IP: " HUB_IP
    
    if [ -z "$HUB_IP" ]; then
        echo "❌ Hub IP is required!"
        exit 1
    fi
    
    # Test connection
    echo "Testing connection to http://${HUB_IP}:8000..."
    if curl -s --connect-timeout 5 "http://${HUB_IP}:8000/" > /dev/null 2>&1; then
        echo "✓ Connected!"
    else
        echo "⚠ Could not reach http://${HUB_IP}:8000"
        echo "  Make sure the hub device is running and on the same network."
        read -p "Continue anyway? (y/n): " CONTINUE
        if [ "$CONTINUE" != "y" ]; then
            exit 1
        fi
    fi
    
    export LOCAL_IP
    export SERVER_URL="http://${HUB_IP}:8000"
    export LISTEN_PORT=9000
    
    # Find agent binary
    if [ -f "./target/release/vishwarupa" ]; then
        AGENT="./target/release/vishwarupa"
    elif [ -f "./vishwarupa" ]; then
        AGENT="./vishwarupa"
    else
        echo "Building agent (first time only, may take a few minutes)..."
        if command -v cargo &> /dev/null; then
            cargo build --release
            AGENT="./target/release/vishwarupa"
        else
            echo "❌ Rust not installed! Install:"
            echo "   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
            exit 1
        fi
    fi
    
    echo ""
    echo "Starting agent..."
    $AGENT &
    AGENT_PID=$!
    
    sleep 2
    
    if kill -0 $AGENT_PID 2>/dev/null; then
        echo ""
        echo "╔═══════════════════════════════════════════════════════════╗"
        echo "║              ✓ JOINED THE NETWORK                         ║"
        echo "╠═══════════════════════════════════════════════════════════╣"
        echo "║                                                           ║"
        echo "║  📱 Open this URL on ANY device:                          ║"
        echo "║                                                           ║"
        echo "║     👉  http://${HUB_IP}:8000                             "
        echo "║                                                           ║"
        echo "║  ✓ This device is now storing shards                      ║"
        echo "║  ✓ Files uploaded will be distributed here too            ║"
        echo "║                                                           ║"
        echo "╚═══════════════════════════════════════════════════════════╝"
        echo ""
        echo "Press Ctrl+C to stop..."
        
        cleanup() {
            echo ""
            echo "Stopping agent..."
            kill $AGENT_PID 2>/dev/null || true
            exit 0
        }
        trap cleanup SIGINT SIGTERM
        wait $AGENT_PID
    else
        echo "❌ Agent failed to start"
        exit 1
    fi
fi

