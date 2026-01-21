#!/bin/bash
# ============================================
# VISHWARUPA - START PHONE/TERMUX
# ============================================
# Starts both the web server and agent on phone
# ============================================

set -e

echo "🌐 Starting Vishwarupa on Phone/Tablet..."
echo ""

# Navigate to script directory (where this script is located)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
echo "📂 Working directory: $SCRIPT_DIR"

# Detect environment and source Python
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Create master key if not exists
if [ ! -f "master_9000.key" ]; then
    echo "⚠ No master key found. Creating one..."
    echo ""
    echo "IMPORTANT: Use the SAME password as your laptop!"
    echo "If this is your first device, choose a strong password."
    echo ""
    
    # Find agent binary first
    if [ -f "./target/release/vishwarupa" ]; then
        TEMP_AGENT="./target/release/vishwarupa"
    elif [ -f "./vishwarupa" ]; then
        TEMP_AGENT="./vishwarupa"
    else
        TEMP_AGENT=""
    fi
    
    # Run agent briefly to trigger key generation
    if [ -n "$TEMP_AGENT" ]; then
        export LISTEN_PORT=9000
        timeout 5 $TEMP_AGENT id 2>/dev/null || true
    fi
    
    if [ ! -f "master_9000.key" ]; then
        echo "Generating default test key..."
        echo "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" > master_9000.key
    fi
fi

# Get local IP address
get_local_ip() {
    # Try different methods for different environments
    if command -v ip &> /dev/null; then
        ip addr show 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | cut -d/ -f1
    elif command -v ifconfig &> /dev/null; then
        ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}'
    elif command -v hostname &> /dev/null; then
        hostname -I 2>/dev/null | awk '{print $1}'
    else
        echo ""
    fi
}

LOCAL_IP=$(get_local_ip)

# If LOCAL_IP is empty or localhost, ask user
if [ -z "$LOCAL_IP" ] || [ "$LOCAL_IP" = "localhost" ] || [ "$LOCAL_IP" = "127.0.0.1" ]; then
    echo ""
    echo "⚠ Could not detect your phone's IP address automatically."
    echo "This IP is needed so other devices can send shards to your phone."
    echo ""
    echo "To find your IP: Settings → Wi-Fi → tap connected network → IP address"
    read -p "Enter your phone's Wi-Fi IP address: " LOCAL_IP
fi

if [ -n "$LOCAL_IP" ] && [ "$LOCAL_IP" != "127.0.0.1" ]; then
    export LOCAL_IP
    echo "✓ Phone IP: $LOCAL_IP"
fi

# Check for laptop server URL
if [ -z "$SERVER_URL" ]; then
    echo ""
    echo "Do you want to connect to a laptop server?"
    echo "(If running standalone on phone, press Enter to skip)"
    read -p "Enter laptop IP (e.g., 192.168.1.100) or press Enter: " LAPTOP_IP
    
    if [ -n "$LAPTOP_IP" ]; then
        export SERVER_URL="http://${LAPTOP_IP}:8000"
        echo "✓ Will connect to server at $SERVER_URL"
    fi
fi

echo ""
echo "Starting web server on port 8000..."

# Find Python 3 command
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "❌ Python not found! Install with: apt install python3"
    exit 1
fi

echo "Using: $PYTHON_CMD"

# Check if server.py exists
if [ ! -f "server.py" ]; then
    echo "❌ server.py not found in $SCRIPT_DIR"
    echo "Make sure you're running from the project directory"
    exit 1
fi

# Start the server
$PYTHON_CMD server.py &
SERVER_PID=$!

# Wait for server to be fully ready
echo "Waiting for server to start..."
SERVER_READY=false
for i in {1..15}; do
    if curl -s http://localhost:8000 > /dev/null 2>&1; then
        echo "✓ Server is ready!"
        SERVER_READY=true
        break
    fi
    sleep 1
    echo "  Waiting... ($i/15)"
done

if [ "$SERVER_READY" = false ]; then
    echo "⚠ Server may not have started properly"
    echo "  Check if port 8000 is available"
fi

echo ""
echo "Starting agent on port 9000..."
export LISTEN_PORT=9000

# Find the agent binary
if [ -f "./target/release/vishwarupa" ]; then
    AGENT_BIN="./target/release/vishwarupa"
elif [ -f "./vishwarupa" ]; then
    AGENT_BIN="./vishwarupa"
else
    echo "❌ Agent binary not found!"
    echo "Build it first with: cargo build --release"
    kill $SERVER_PID 2>/dev/null
    exit 1
fi

echo "Using agent: $AGENT_BIN"
$AGENT_BIN &
AGENT_PID=$!

# Give agent time to start
sleep 3

echo ""
echo "================================================"
echo "  ✓ VISHWARUPA IS RUNNING"
echo "================================================"
echo ""
echo "📱 Open browser on this device:"
echo "   http://localhost:8000"
echo ""
if [ -n "$LOCAL_IP" ] && [ "$LOCAL_IP" != "localhost" ]; then
    echo "🌐 Or access from other devices on same network:"
    echo "   http://$LOCAL_IP:8000"
    echo ""
fi
if [ -n "$SERVER_URL" ]; then
    echo "🔗 Connected to laptop server:"
    echo "   $SERVER_URL"
    echo ""
fi
echo "Press Ctrl+C to stop..."
echo ""

# Cleanup on exit
cleanup() {
    echo ""
    echo "Stopping Vishwarupa..."
    kill $SERVER_PID 2>/dev/null
    kill $AGENT_PID 2>/dev/null
    exit 0
}

trap cleanup INT TERM

# Keep running
wait