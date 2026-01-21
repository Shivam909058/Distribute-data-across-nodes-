#!/bin/bash
# ============================================
# VISHWARUPA - START PHONE/TERMUX
# ============================================
# Starts both the web server and agent on phone
# ============================================

echo "🌐 Starting Vishwarupa on Phone/Tablet..."
echo ""

# Navigate to script directory
cd "$(dirname "$0")"

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
    
    # Run agent briefly to trigger key generation
    export LISTEN_PORT=9000
    timeout 5 ./target/release/vishwarupa id || true
    
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
    else
        echo "localhost"
    fi
}

LOCAL_IP=$(get_local_ip)

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
python server.py &
SERVER_PID=$!

# Wait for server to be fully ready
echo "Waiting for server to start..."
for i in {1..15}; do
    if curl -s http://localhost:8000 > /dev/null 2>&1; then
        echo "✓ Server is ready!"
        break
    fi
    sleep 1
done

echo ""
echo "Starting agent on port 9000..."
export LISTEN_PORT=9000
./target/release/vishwarupa &
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