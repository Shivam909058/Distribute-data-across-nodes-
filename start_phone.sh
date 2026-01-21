#!/bin/bash
# ============================================
# VISHWARUPA - PHONE AGENT ONLY
# ============================================
# This runs ONLY the agent on phone.
# The web server runs on laptop - use laptop's IP to access UI.
# ============================================

set -e

echo ""
echo "================================================"
echo "  🌐 VISHWARUPA PHONE AGENT"
echo "================================================"
echo ""

cd "$(dirname "$0")"
echo "📂 Working directory: $(pwd)"

# Activate venv if exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Get phone's IP
get_local_ip() {
    ip route get 1 2>/dev/null | awk '{print $7; exit}' || \
    hostname -I 2>/dev/null | awk '{print $1}' || \
    ip addr show 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | cut -d/ -f1 || \
    echo ""
}

LOCAL_IP=$(get_local_ip)

if [ -z "$LOCAL_IP" ] || [ "$LOCAL_IP" = "127.0.0.1" ]; then
    echo "⚠ Could not detect your phone's IP address automatically."
    echo "  To find your IP: Settings → Wi-Fi → tap connected network → IP address"
    echo ""
    read -p "Enter your phone's Wi-Fi IP address: " LOCAL_IP
fi

echo "✓ Phone IP: $LOCAL_IP"
echo ""

# Get laptop server IP (REQUIRED)
echo "The server runs on your LAPTOP. Enter the laptop's IP address:"
echo "(On laptop, run 'ipconfig' or check Wi-Fi settings)"
echo ""
read -p "Laptop IP: " LAPTOP_IP

if [ -z "$LAPTOP_IP" ]; then
    echo ""
    echo "✗ Laptop IP is required!"
    echo ""
    echo "Steps:"
    echo "  1. On LAPTOP: run 'python server.py' and 'run_agent.bat'"
    echo "  2. On LAPTOP: run 'ipconfig' to find its IP (e.g., 10.80.146.52)"
    echo "  3. On PHONE: run this script again and enter the laptop IP"
    echo ""
    exit 1
fi

SERVER_URL="http://${LAPTOP_IP}:8000"
echo "✓ Will connect to server at $SERVER_URL"
echo ""

# Export environment variables
export LOCAL_IP
export SERVER_URL
export LISTEN_PORT=9000

# Find agent binary
if [ -f "./target/release/vishwarupa" ]; then
    AGENT="./target/release/vishwarupa"
elif [ -f "./vishwarupa" ]; then
    AGENT="./vishwarupa"
else
    echo "Agent not found! Building..."
    if command -v cargo &> /dev/null; then
        cargo build --release
        AGENT="./target/release/vishwarupa"
    else
        echo "✗ Rust/Cargo not installed!"
        echo "  Install with: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
        exit 1
    fi
fi

# Create master key if needed (same as laptop)
if [ ! -f "master_9000.key" ]; then
    echo ""
    echo "⚠ No master key found."
    echo "IMPORTANT: Use the SAME password as your laptop!"
    echo ""
fi

echo "Starting agent on port 9000..."
echo "Using: $AGENT"
echo ""

# Run agent (foreground)
$AGENT &
AGENT_PID=$!

sleep 2

if kill -0 $AGENT_PID 2>/dev/null; then
    echo ""
    echo "================================================"
    echo "  ✓ PHONE AGENT IS RUNNING"
    echo "================================================"
    echo ""
    echo "📱 Your phone is now part of the storage network!"
    echo ""
    echo "🌐 To upload/download files, open browser and go to:"
    echo ""
    echo "   👉 http://${LAPTOP_IP}:8000"
    echo ""
    echo "   (This is the ONLY URL you need - works from any device)"
    echo ""
    echo "📊 Shards stored on this phone: ./data_*/"
    echo ""
    echo "Press Ctrl+C to stop..."
    echo ""
    
    # Handle shutdown
    cleanup() {
        echo ""
        echo "Stopping agent..."
        kill $AGENT_PID 2>/dev/null || true
        exit 0
    }
    trap cleanup SIGINT SIGTERM
    
    wait $AGENT_PID
else
    echo "✗ Agent failed to start"
    exit 1
fi
