#!/bin/bash
echo "🌐 Starting Vishwarupa on Phone/Tablet..."
echo ""

if [ ! -f "master_9000.key" ]; then
    echo "Creating master key..."
    echo "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" > master_9000.key
fi

echo "Starting web server on port 8000..."
python server.py &
SERVER_PID=$!

# Wait for server to be fully ready
echo "Waiting for server to start..."
sleep 5

# Verify server is responding
for i in {1..10}; do
    if curl -s http://localhost:8000 > /dev/null 2>&1; then
        echo "Server is ready!"
        break
    fi
    sleep 1
done

echo "Starting agent on port 9000..."
export LISTEN_PORT=9000
./target/release/vishwarupa &
AGENT_PID=$!

echo ""
echo "✓ Vishwarupa is running!"
echo ""
echo "📱 Open browser and go to:"
echo "   http://localhost:8000"
echo ""
echo "Or from another device on same network:"
IP=$(ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1)
if [ -n "$IP" ]; then
    echo "   http://$IP:8000"
fi
echo ""
echo "Press Ctrl+C to stop..."
echo ""

trap "echo ''; echo 'Stopping...'; kill $SERVER_PID $AGENT_PID 2>/dev/null; exit" INT TERM
wait