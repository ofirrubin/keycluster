#!/bin/bash

# One-Shot Integration Test for Keycluster
# 1. Starts tunnels
# 2. Runs integration test
# 3. Cleans up everything automatically

# Start tunnels in the background, but use a separate process group so we can kill easily
./tests/test-tunnel.sh &
TUNNEL_PID=$!

# Give tunnels a moment to breathe
sleep 5

# Run the actual test
if ./tests/integration_test.sh; then
    echo "✨ Integration Test SUCCESS"
else
    echo "❌ Integration Test FAILED"
    kill $TUNNEL_PID
    exit 1
fi

# Cleanup
kill $TUNNEL_PID
wait $TUNNEL_PID 2>/dev/null || true
echo "🛑 Tunnels closed. Cleanup complete."
