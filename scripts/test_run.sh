#!/bin/bash
# test_run.sh - Script to build, install, and test the aion-agent-api package
# 
# This script automates the process of:
# 1. Building the aion-agent-api package
# 2. Uninstalling it from agent-workflow
# 3. Reinstalling it in agent-workflow
# 4. Running "poetry run aion serve" with optional debugging
#
# Usage:
#   ./scripts/test_run.sh                   # Regular run without debugging
#   ./scripts/test_run.sh --debug           # Run with debugger enabled and waiting
#   ./scripts/test_run.sh --debug-port 9999 # Specify a custom debug port

set -e  # Exit on any error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Directory paths
AION_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
AGENT_PROJECT_DIR="${AGENT_PROJECT_DIR:-../agent-workflow}"

echo -e "${BLUE}=== Starting test-run process ===${NC}"

# Change to the aion-agent-api directory
cd "$AION_DIR"
echo -e "${BLUE}Current directory: $(pwd)${NC}"

# Build the package
echo -e "${BLUE}Building aion-agent-api package...${NC}"
poetry build
echo -e "${GREEN}Build completed successfully.${NC}"

# Get the latest version from pyproject.toml
VERSION=$(grep "^version =" pyproject.toml | sed 's/version = "\(.*\)"/\1/')
echo -e "${BLUE}Package version: ${VERSION}${NC}"

# Change to the agent-workflow directory
cd "$AGENT_PROJECT_DIR"
echo -e "${BLUE}Changed to agent-workflow directory: $(pwd)${NC}"

# Uninstall existing aion-agent-api package
echo -e "${BLUE}Removing existing aion-agent-api package...${NC}"
poetry remove aion-agent-api || echo -e "${RED}No existing package to remove, continuing...${NC}"

# Install the newly built package
echo -e "${BLUE}Installing new aion-agent-api package...${NC}"
poetry add "$AION_DIR/dist/aion_agent_api-${VERSION}-py3-none-any.whl"
echo -e "${GREEN}Installation completed successfully.${NC}"

# Process command line arguments to handle debugging options
DEBUG_ENABLED=true
DEBUG_PORT=5678
DEBUG_WAIT=false
EXTRA_ARGS=""

# Parse the command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --debug)
            DEBUG_ENABLED=true
            DEBUG_WAIT=true
            shift
            ;;
        --debug-port)
            DEBUG_PORT="$2"
            shift 2
            ;;
        --wait-for-client)
            DEBUG_WAIT=true
            shift
            ;;
        *) # Unknown option
            EXTRA_ARGS="$EXTRA_ARGS $1"
            shift
            ;;
    esac
done

# Build the server command
SERVE_CMD="poetry run aion serve"

# Add debug options if enabled
if [ "$DEBUG_ENABLED" = true ]; then
    echo -e "${BLUE}🐛 Debugging enabled on port $DEBUG_PORT${NC}"
    SERVE_CMD="$SERVE_CMD --debug-port $DEBUG_PORT"
    
    if [ "$DEBUG_WAIT" = true ]; then
        echo -e "${BLUE}⏳ Server will wait for debugger to attach${NC}"
        SERVE_CMD="$SERVE_CMD --wait-for-client"
    fi
fi

# Add any extra arguments
SERVE_CMD="$SERVE_CMD $EXTRA_ARGS"

# Run the server
echo -e "${BLUE}Running: $SERVE_CMD${NC}"
eval $SERVE_CMD

# Script will exit here if `aion serve` is stopped with Ctrl+C
echo -e "${GREEN}Test run completed.${NC}"
