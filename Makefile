# Prismis Makefile
# Modern installation using uv and ~/.local/bin

# XDG Base Directory support
XDG_CONFIG_HOME ?= $(HOME)/.config
XDG_DATA_HOME ?= $(HOME)/.local/share

INSTALL_DIR := $(HOME)/.local/bin
CONFIG_DIR := $(XDG_CONFIG_HOME)/prismis
DATA_DIR := $(XDG_DATA_HOME)/prismis
LAUNCHD_DIR := $(HOME)/Library/LaunchAgents

.PHONY: help
help: ## Show available targets
	@echo "Prismis Build & Installation"
	@echo "============================"
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

.PHONY: check-deps
check-deps: ## Check and install required dependencies
	@echo "Checking dependencies..."
	@if ! command -v go >/dev/null 2>&1; then \
		echo "❌ Go is not installed. Please install Go 1.21+ first."; \
		echo "   Visit: https://go.dev/doc/install"; \
		exit 1; \
	else \
		echo "✓ Go is installed"; \
	fi
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "⚠️  uv is not installed. Installing uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		export PATH="$$HOME/.cargo/bin:$$PATH"; \
		echo "✓ uv installed"; \
	else \
		echo "✓ uv is installed"; \
	fi
	@if ! uv python list 2>/dev/null | grep -q "cpython-3\.13"; then \
		echo "⚠️  Python 3.13 not found. Installing..."; \
		uv python install 3.13; \
		echo "✓ Python 3.13 installed"; \
	else \
		echo "✓ Python 3.13 is available"; \
	fi

.PHONY: build
build: check-deps build-tui build-daemon build-cli ## Build all components

.PHONY: build-tui
build-tui: ## Build Go TUI binary
	@echo "Building Go TUI..."
	cd tui && go mod download
	cd tui && go build -o prismis cmd/prismis/main.go
	@echo "✓ TUI built: tui/prismis"

.PHONY: build-daemon
build-daemon: ## Setup Python daemon dependencies
	@echo "Setting up Python daemon..."
	cd daemon && uv sync
	@echo "✓ Daemon ready"

.PHONY: build-cli
build-cli: ## Setup Python CLI dependencies
	@echo "Setting up Python CLI..."
	cd cli && uv sync
	@echo "✓ CLI ready"

.PHONY: install
install: check-deps build stop-daemon install-binaries install-config restart-daemon ## Install everything (binaries + config) with daemon restart
	@echo "========================================="
	@echo "Installation complete!"
	@echo "Binaries installed to: $(INSTALL_DIR)"
	@echo "Config files in: $(CONFIG_DIR)"
	@echo ""
	@echo "Next steps:"
	@echo "  prismis-cli source add https://example.com/feed"
	@echo "  prismis  # Launch TUI"
	@echo "========================================="

.PHONY: install-binaries
install-binaries: ## Install binaries to ~/.local/bin
	@echo "Installing to $(INSTALL_DIR)..."
	@mkdir -p $(INSTALL_DIR)
	# Install Go TUI
	@if [ -f tui/prismis ]; then \
		cp tui/prismis $(INSTALL_DIR)/prismis; \
		chmod +x $(INSTALL_DIR)/prismis; \
		if [[ "$$(uname)" == "Darwin" ]]; then \
			codesign --remove-signature $(INSTALL_DIR)/prismis 2>/dev/null; \
			codesign -s - $(INSTALL_DIR)/prismis; \
			echo "✓ Installed and signed prismis TUI"; \
		else \
			echo "✓ Installed prismis TUI"; \
		fi; \
	else \
		echo "✗ TUI not built. Run 'make build-tui' first"; \
		exit 1; \
	fi
	# Install Python daemon via uv tool (use --reinstall for proper Python version)
	cd daemon && uv tool install . --python 3.13 --reinstall
	@echo "✓ Installed prismis-daemon"
	# Install Python CLI via uv tool
	cd cli && uv tool install . --python 3.13 --reinstall
	@echo "✓ Installed prismis-cli"
	# Check PATH
	@if [[ ":$$PATH:" != *":$(INSTALL_DIR):"* ]]; then \
		echo ""; \
		echo "⚠️  $(INSTALL_DIR) is not in your PATH"; \
		echo "Add to your shell profile: export PATH=\"\$$HOME/.local/bin:\$$PATH\""; \
	fi

.PHONY: install-config
install-config: ## Create default config files (never overwrites existing)
	@echo "Setting up configuration..."
	@mkdir -p $(CONFIG_DIR)
	@mkdir -p $(DATA_DIR)
	# Create default config.toml if not exists
	@if [ ! -f $(CONFIG_DIR)/config.toml ]; then \
		echo "Creating default config.toml..."; \
		echo '[daemon]' > $(CONFIG_DIR)/config.toml; \
		echo 'fetch_interval = 30  # minutes' >> $(CONFIG_DIR)/config.toml; \
		echo 'batch_size = 10' >> $(CONFIG_DIR)/config.toml; \
		echo 'retry_attempts = 3' >> $(CONFIG_DIR)/config.toml; \
		echo '' >> $(CONFIG_DIR)/config.toml; \
		echo '[llm]' >> $(CONFIG_DIR)/config.toml; \
		echo 'provider = "openai"' >> $(CONFIG_DIR)/config.toml; \
		echo 'model = "gpt-4o-mini"' >> $(CONFIG_DIR)/config.toml; \
		echo '# api_key = "your-api-key-here"  # Or use OPENAI_API_KEY env var' >> $(CONFIG_DIR)/config.toml; \
		echo '' >> $(CONFIG_DIR)/config.toml; \
		echo '[notifications]' >> $(CONFIG_DIR)/config.toml; \
		echo 'enabled = true' >> $(CONFIG_DIR)/config.toml; \
		echo 'priority_threshold = "high"' >> $(CONFIG_DIR)/config.toml; \
		echo 'quiet_hours_start = "22:00"' >> $(CONFIG_DIR)/config.toml; \
		echo 'quiet_hours_end = "08:00"' >> $(CONFIG_DIR)/config.toml; \
		echo '' >> $(CONFIG_DIR)/config.toml; \
		echo '# Database location is handled automatically using XDG standards' >> $(CONFIG_DIR)/config.toml; \
		echo '# Database will be in $$XDG_DATA_HOME/prismis/prismis.db' >> $(CONFIG_DIR)/config.toml; \
		echo "✓ Created config.toml"; \
	else \
		echo "✓ config.toml already exists (preserved)"; \
	fi
	# Create default context.md if not exists
	@if [ ! -f $(CONFIG_DIR)/context.md ]; then \
		echo "Creating default context.md..."; \
		echo '# Personal Context for Content Prioritization' > $(CONFIG_DIR)/context.md; \
		echo '' >> $(CONFIG_DIR)/context.md; \
		echo '## High Priority Topics' >> $(CONFIG_DIR)/context.md; \
		echo '- AI/LLM breakthroughs, especially local models' >> $(CONFIG_DIR)/context.md; \
		echo '- Important security vulnerabilities in my stack' >> $(CONFIG_DIR)/context.md; \
		echo '- Breaking changes in tools I use daily' >> $(CONFIG_DIR)/context.md; \
		echo '' >> $(CONFIG_DIR)/context.md; \
		echo '## Medium Priority Topics' >> $(CONFIG_DIR)/context.md; \
		echo '- General programming best practices' >> $(CONFIG_DIR)/context.md; \
		echo '- New tool releases' >> $(CONFIG_DIR)/context.md; \
		echo '- Performance optimization techniques' >> $(CONFIG_DIR)/context.md; \
		echo '' >> $(CONFIG_DIR)/context.md; \
		echo '## Low Priority Topics' >> $(CONFIG_DIR)/context.md; \
		echo '- Programming tutorials for beginners' >> $(CONFIG_DIR)/context.md; \
		echo '- General tech news' >> $(CONFIG_DIR)/context.md; \
		echo '- Conference announcements' >> $(CONFIG_DIR)/context.md; \
		echo '' >> $(CONFIG_DIR)/context.md; \
		echo '## Not Interested' >> $(CONFIG_DIR)/context.md; \
		echo '- Crypto, blockchain, web3' >> $(CONFIG_DIR)/context.md; \
		echo '- Gaming news' >> $(CONFIG_DIR)/context.md; \
		echo '- Politics' >> $(CONFIG_DIR)/context.md; \
		echo "✓ Created context.md"; \
	else \
		echo "✓ context.md already exists (preserved)"; \
	fi
	# Initialize database
	@echo "Initializing database..."
	@cd daemon && uv run python -c "from prismis_daemon.database import init_db; init_db()" 2>/dev/null || echo "✓ Database ready"

.PHONY: install-daemon
install-daemon: ## Install launchd service WITHOUT starting (macOS)
	@echo "Installing daemon service (NOT auto-starting)..."
	@if [[ "$$(uname)" == "Darwin" ]]; then \
		mkdir -p $(LAUNCHD_DIR); \
		mkdir -p $(HOME)/Library/Logs; \
		cp scripts/com.prismis.daemon.plist $(LAUNCHD_DIR)/; \
		DAEMON_PATH=$$(which prismis-daemon 2>/dev/null || echo "$(INSTALL_DIR)/prismis-daemon"); \
		sed -i '' "s|DAEMON_PATH_PLACEHOLDER|$$DAEMON_PATH|g" $(LAUNCHD_DIR)/com.prismis.daemon.plist; \
		sed -i '' "s|LOG_PATH_PLACEHOLDER|$(HOME)/Library/Logs|g" $(LAUNCHD_DIR)/com.prismis.daemon.plist; \
		sed -i '' "s|HOME_PATH_PLACEHOLDER|$(HOME)|g" $(LAUNCHD_DIR)/com.prismis.daemon.plist; \
		echo "✓ Daemon service installed but NOT activated"; \
		echo ""; \
		echo "To activate auto-start:"; \
		echo "  launchctl load $(LAUNCHD_DIR)/com.prismis.daemon.plist"; \
		echo ""; \
		echo "To run manually:"; \
		echo "  prismis-daemon"; \
	else \
		echo "✗ This target is for macOS only. For Linux, use systemd."; \
	fi

.PHONY: stop-daemon
stop-daemon: ## Stop the daemon service gracefully
	@echo "Stopping daemon service (if running)..."
	@# First kill any processes using port 8989
	@lsof -ti:8989 | xargs kill -9 2>/dev/null || true
	@# Then kill any prismis-daemon processes
	@pkill -f prismis-daemon 2>/dev/null || true
	@pkill -f "python.*prismis_daemon" 2>/dev/null || true
	@if [[ "$$(uname)" == "Darwin" ]]; then \
		if launchctl list | grep -q com.prismis.daemon; then \
			launchctl stop com.prismis.daemon 2>/dev/null || true; \
			launchctl unload $(LAUNCHD_DIR)/com.prismis.daemon.plist 2>/dev/null || true; \
		fi; \
	fi
	@echo "✓ Daemon service stopped"

.PHONY: restart-daemon
restart-daemon: install-daemon ## Restart the daemon service after installation (production)
	@echo "Starting daemon service via launchctl..."
	@if [[ "$$(uname)" == "Darwin" ]]; then \
		launchctl load -w $(LAUNCHD_DIR)/com.prismis.daemon.plist 2>/dev/null || true; \
		sleep 3; \
		if launchctl list | grep -q com.prismis.daemon; then \
			echo "✓ Daemon service started and registered with launchctl"; \
			echo "  Status: launchctl list | grep prismis"; \
			echo "  Logs: tail -f ~/Library/Logs/prismis-daemon.log"; \
		else \
			echo "❌ Daemon service failed to start"; \
			echo "  Check error log: tail -f ~/Library/Logs/prismis-daemon.error.log"; \
			exit 1; \
		fi; \
	else \
		echo "✓ Linux: Start daemon manually with: prismis-daemon"; \
	fi

.PHONY: start-daemon
start-daemon: ## Actually start the daemon service
	@if [[ "$$(uname)" == "Darwin" ]]; then \
		launchctl load $(LAUNCHD_DIR)/com.prismis.daemon.plist 2>/dev/null || true; \
		launchctl start com.prismis.daemon; \
		echo "✓ Daemon service activated and started"; \
		echo "Check status: launchctl list | grep prismis"; \
	else \
		echo "✗ This target is for macOS only."; \
	fi

.PHONY: uninstall
uninstall: ## Remove all installed components
	@echo "Uninstalling Prismis..."
	# Stop and remove daemon service
	@if [[ "$$(uname)" == "Darwin" ]] && [ -f $(LAUNCHD_DIR)/com.prismis.daemon.plist ]; then \
		launchctl unload $(LAUNCHD_DIR)/com.prismis.daemon.plist 2>/dev/null || true; \
		rm -f $(LAUNCHD_DIR)/com.prismis.daemon.plist; \
		echo "✓ Removed daemon service"; \
	fi
	# Remove binaries
	@rm -f $(INSTALL_DIR)/prismis
	@uv tool uninstall prismis-daemon 2>/dev/null || true
	@uv tool uninstall prismis-cli 2>/dev/null || true
	@echo "✓ Removed binaries"
	# Ask about config/data
	@echo ""
	@echo "Config and data preserved in $(CONFIG_DIR)"
	@echo "To remove completely: rm -rf $(CONFIG_DIR)"

.PHONY: dev
dev: ## Run daemon in development mode
	cd daemon && PYTHONPATH=src uv run python -m src --once

.PHONY: dev-tui
dev-tui: ## Run TUI in development mode
	cd tui && go run cmd/prismis/main.go

.PHONY: dev-cli
dev-cli: ## Run CLI in development mode
	cd cli && uv run python -m cli

.PHONY: test
test: ## Run all tests
	@echo "Running daemon tests..."
	cd daemon && uv run pytest tests/ -v
	@echo "Running TUI tests..."
	cd tui && go test ./...
	@echo "Running CLI tests..."
	cd cli && uv run pytest tests/ -v

.PHONY: clean
clean: ## Clean build artifacts
	rm -f tui/prismis
	rm -rf daemon/.venv
	rm -rf cli/.venv
	rm -rf daemon/dist
	rm -rf cli/dist
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	@echo "✓ Cleaned build artifacts"

.PHONY: status
status: ## Check installation status
	@echo "Installation Status:"
	@echo "===================="
	@which prismis >/dev/null 2>&1 && echo "✓ TUI installed: $$(which prismis)" || echo "✗ TUI not found"
	@which prismis-daemon >/dev/null 2>&1 && echo "✓ Daemon installed: $$(which prismis-daemon)" || echo "✗ Daemon not found"
	@which prismis-cli >/dev/null 2>&1 && echo "✓ CLI installed: $$(which prismis-cli)" || echo "✗ CLI not found"
	@[ -f $(CONFIG_DIR)/config.toml ] && echo "✓ Config exists: $(CONFIG_DIR)/config.toml" || echo "✗ Config not found"
	@[ -f $(CONFIG_DIR)/context.md ] && echo "✓ Context exists: $(CONFIG_DIR)/context.md" || echo "✗ Context not found"
	@[ -f $(CONFIG_DIR)/prismis.db ] && echo "✓ Database exists: $(CONFIG_DIR)/prismis.db" || echo "✗ Database not found"
	@if [[ "$$(uname)" == "Darwin" ]]; then \
		launchctl list | grep -q prismis && echo "✓ Daemon service running" || echo "✗ Daemon service not running"; \
	fi

# Default target
.DEFAULT_GOAL := help