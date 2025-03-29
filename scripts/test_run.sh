#!/bin/bash
# test_run.sh - Script to build, install, and test the aion-agent-api package
# 
# This script automates the process of:
# 1. Building the aion-agent-api package
# 2. Uninstalling it from agent-workflow
# 3. Reinstalling it in agent-workflow
# 4. Running "poetry run aion serve"

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

# Run the server
echo -e "${BLUE}Running 'aion serve'...${NC}"
poetry run aion serve

# Script will exit here if `aion serve` is stopped with Ctrl+C
echo -e "${GREEN}Test run completed.${NC}"
