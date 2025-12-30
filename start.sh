#!/bin/bash

# Vishwarupa Quick Start Script

echo "🌐 Vishwarupa - Decentralized Personal Storage"
echo "==============================================="
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.11+"
    exit 1
fi

# Check if Rust is installed
if ! command -v cargo &> /dev/null; then
    echo "❌ Rust is not installed. Install from https://rustup.rs"
    exit 1
fi

echo "✓ Python found: $(python3 --version)"
echo "✓ Rust found: $(cargo --version)"
echo ""

# Install Python dependencies
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Installing Python dependencies..."
source venv/bin/activate
pip install -q -r requirements.txt

# Build Rust agent
echo ""
echo "Building agent..."
cargo build --release

if [ $? -ne 0 ]; then
    echo "❌ Build failed"
    exit 1
fi

echo ""
echo "✓ Build complete!"
echo ""
echo "==============================================="
echo "Next Steps:"
echo "==============================================="
echo ""
echo "1. Start the controller (in terminal 1):"
echo "   ./venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000"
echo ""
echo "2. Open browser:"
echo "   http://localhost:8000"
echo ""
echo "3. Run agent daemon (in terminal 2):"
echo "   ./target/release/agent"
echo ""
echo "4. Run agents on other devices, then upload:"
echo "   ./target/release/agent upload myfile.pdf"
echo ""
echo "5. Download on any device:"
echo "   ./target/release/agent download <file_id> output.pdf"
echo ""
echo "==============================================="

