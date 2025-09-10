#!/bin/bash
# Test runner for Prismis daemon

# Set PYTHONPATH to find daemon modules
export PYTHONPATH=src

echo "Running unit tests..."
uv run pytest tests/unit/ -v

echo ""
echo "Running integration tests..."
uv run pytest tests/integration/ -v

echo ""
echo "Running all tests..."
uv run pytest -v