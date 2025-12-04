# Prismis Makefile
# Modern installation using uv and ~/.local/bin

# XDG Base Directory support
XDG_CONFIG_HOME ?= $(HOME)/.config
XDG_DATA_HOME ?= $(HOME)/.local/share

INSTALL_DIR := $(HOME)/.local/bin
CONFIG_DIR := $(XDG_CONFIG_HOME)/prismis
DATA_DIR := $(XDG_DATA_HOME)/prismis

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
	cd tui && go build -o prismis ./cmd/prismis
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
install: check-deps build install-binaries install-config ## Install everything (binaries + config)
	@echo "========================================="
	@echo "Installation complete!"
	@echo "Binaries installed to: $(INSTALL_DIR)"
	@echo "Config files in: $(CONFIG_DIR)"
	@echo ""
	@echo "Next steps:"
	@echo "1. Edit ~/.config/prismis/.env and add your API keys"
	@echo "2. Customize ~/.config/prismis/context.md with your interests"
	@echo "3. Start daemon: prismis-daemon &"
	@echo "4. Add sources: prismis-cli source add https://example.com/feed"
	@echo "5. Launch TUI: prismis"
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
install-config: ## Create .env template (config.toml created by daemon on first run)
	@echo "Setting up configuration..."
	@mkdir -p $(CONFIG_DIR)
	@mkdir -p $(DATA_DIR)
	# Note: config.toml and context.md are created by daemon on first run
	# This ensures correct schema with all required fields
	# Create .env template if not exists
	@if [ ! -f $(CONFIG_DIR)/.env ]; then \
		echo "Creating .env template..."; \
		echo '# Prismis Environment Variables' > $(CONFIG_DIR)/.env; \
		echo '# Edit this file and fill in your actual API keys' >> $(CONFIG_DIR)/.env; \
		echo '' >> $(CONFIG_DIR)/.env; \
		echo '# Required: OpenAI API key (or your chosen LLM provider)' >> $(CONFIG_DIR)/.env; \
		echo 'OPENAI_API_KEY=sk-your-key-here' >> $(CONFIG_DIR)/.env; \
		echo '' >> $(CONFIG_DIR)/.env; \
		echo '# Optional: For local models (LM Studio, Ollama), no key needed' >> $(CONFIG_DIR)/.env; \
		echo '# Edit config.toml [llm] section instead' >> $(CONFIG_DIR)/.env; \
		echo '' >> $(CONFIG_DIR)/.env; \
		echo '# Optional: Reddit API credentials (only needed for reddit:// sources)' >> $(CONFIG_DIR)/.env; \
		echo 'REDDIT_CLIENT_ID=your-reddit-client-id' >> $(CONFIG_DIR)/.env; \
		echo 'REDDIT_CLIENT_SECRET=your-reddit-client-secret' >> $(CONFIG_DIR)/.env; \
		echo '' >> $(CONFIG_DIR)/.env; \
		echo '# Optional: ElevenLabs API key (for :audio command with elevenlabs provider)' >> $(CONFIG_DIR)/.env; \
		echo 'ELEVENLABS_API_KEY=your-elevenlabs-key' >> $(CONFIG_DIR)/.env; \
		chmod 600 $(CONFIG_DIR)/.env; \
		echo "✓ Created .env template (edit and add your API keys)"; \
	else \
		echo "✓ .env already exists (preserved)"; \
	fi
	# Initialize database
	@echo "Initializing database..."
	@cd daemon && uv run python -c "from prismis_daemon.database import init_db; init_db()" 2>/dev/null || echo "✓ Database ready"

.PHONY: migrate
migrate: ## Apply database migrations (safe to run multiple times)
	@echo "Applying database migrations..."
	@sqlite3 $(DATA_DIR)/prismis.db "ALTER TABLE content ADD COLUMN archived_at TIMESTAMP;" 2>/dev/null || echo "  ✓ archived_at column exists"
	@sqlite3 $(DATA_DIR)/prismis.db "CREATE INDEX IF NOT EXISTS idx_content_archived ON content(archived_at);"
	@sqlite3 $(DATA_DIR)/prismis.db "ALTER TABLE content ADD COLUMN interesting_override BOOLEAN DEFAULT 0;" 2>/dev/null || echo "  ✓ interesting_override column exists"
	@sqlite3 $(DATA_DIR)/prismis.db "CREATE INDEX IF NOT EXISTS idx_content_interesting ON content(interesting_override);"
	@echo "Migrating sources table to support file type..."
	@sqlite3 $(DATA_DIR)/prismis.db "DROP TABLE IF EXISTS sources_backup;"
	@sqlite3 $(DATA_DIR)/prismis.db "CREATE TABLE sources_backup AS SELECT * FROM sources;"
	@sqlite3 $(DATA_DIR)/prismis.db "DROP TABLE sources;"
	@sqlite3 $(DATA_DIR)/prismis.db "CREATE TABLE sources (id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, type TEXT NOT NULL CHECK(type IN ('rss', 'reddit', 'youtube', 'file')), name TEXT, active BOOLEAN DEFAULT 1, error_count INTEGER DEFAULT 0, last_error TEXT, last_fetched_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
	@sqlite3 $(DATA_DIR)/prismis.db "INSERT INTO sources SELECT * FROM sources_backup;"
	@sqlite3 $(DATA_DIR)/prismis.db "DROP TABLE sources_backup;"
	@sqlite3 $(DATA_DIR)/prismis.db "CREATE INDEX IF NOT EXISTS idx_sources_active ON sources(active);"
	@sqlite3 $(DATA_DIR)/prismis.db "CREATE INDEX IF NOT EXISTS idx_content_priority ON content(priority);"
	@sqlite3 $(DATA_DIR)/prismis.db "CREATE INDEX IF NOT EXISTS idx_content_read ON content(read);"
	@echo "✓ Migration complete"

.PHONY: stop
stop: ## Stop any running prismis processes
	@echo "Stopping prismis processes..."
	@# Kill any processes using port 8989
	@# Use variable capture instead of pipe to avoid xargs running kill with no args
	@pids=$$(lsof -ti:8989 2>/dev/null || true); \
	if [ -n "$$pids" ]; then \
        	kill -9 $$pids 2>/dev/null || true; \
	fi
	@# Kill any prismis-daemon processes
	@pkill -f prismis-daemon 2>/dev/null || true
	@pkill -f "python.*prismis_daemon" 2>/dev/null || true
	@echo "✓ All prismis processes stopped"

.PHONY: start
start: ## Start the prismis daemon
	@echo "Starting prismis daemon..."
	@if command -v prismis-daemon >/dev/null 2>&1; then \
		prismis-daemon & \
		echo "✓ Daemon started"; \
		echo "Run 'prismis' to launch the TUI"; \
	else \
		echo "❌ prismis-daemon not found. Run 'make install' first"; \
		exit 1; \
	fi

.PHONY: restart
restart: stop start ## Restart the prismis daemon
	@echo "✓ Daemon restarted"



.PHONY: uninstall
uninstall: ## Remove all installed components
	@echo "Uninstalling Prismis..."
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
		pgrep -f prismis-daemon > /dev/null && echo "✓ Daemon running" || echo "✗ Daemon not running"; \
	fi

# Default target
.DEFAULT_GOAL := help
