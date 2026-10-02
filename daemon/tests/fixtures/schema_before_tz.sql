-- Prismis Database Schema
-- SQLite with WAL mode for concurrent access

-- Enable WAL mode and set pragmas for concurrent access
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

-- Categories for organizing sources
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,  -- UUID
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Content sources (RSS feeds, Reddit subs, YouTube channels)
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,  -- UUID
    url TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('rss', 'reddit', 'youtube', 'file')),
    name TEXT,
    active BOOLEAN DEFAULT 1,
    error_count INTEGER DEFAULT 0,
    last_error TEXT,
    last_fetched_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Many-to-many relationship between sources and categories
CREATE TABLE IF NOT EXISTS source_categories (
    source_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, category_id),
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

-- Content items fetched from sources
CREATE TABLE IF NOT EXISTS content (
    id TEXT PRIMARY KEY,  -- UUID
    source_id TEXT,  -- Can be NULL for orphaned favorites
    external_id TEXT UNIQUE NOT NULL,  -- For deduplication
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    content TEXT,  -- Full article/transcript text
    summary TEXT,  -- LLM-generated summary
    analysis JSON,  -- Full LLM analysis (topics, relevance_score, etc)
    priority TEXT CHECK(priority IN ('high', 'medium', 'low', NULL)),
    published_at TIMESTAMP,  -- When source published it
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- When we grabbed it
    read BOOLEAN DEFAULT 0,
    favorited BOOLEAN DEFAULT 0,
    interesting_override BOOLEAN DEFAULT 0,  -- User-flagged for context analysis
    notes TEXT,
    archived_at TIMESTAMP DEFAULT NULL,  -- Soft archival (NULL = active)
    user_feedback TEXT CHECK(user_feedback IN ('up', 'down', NULL)),  -- User feedback: 'up' = useful, 'down' = not useful
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_content_priority ON content(priority);
CREATE INDEX IF NOT EXISTS idx_content_read ON content(read);
CREATE INDEX IF NOT EXISTS idx_content_published ON content(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_source ON content(source_id);
CREATE INDEX IF NOT EXISTS idx_content_fetched ON content(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_archived ON content(archived_at);
CREATE INDEX IF NOT EXISTS idx_content_interesting ON content(interesting_override);
CREATE INDEX IF NOT EXISTS idx_content_user_feedback ON content(user_feedback);
CREATE INDEX IF NOT EXISTS idx_sources_active ON sources(active);
CREATE INDEX IF NOT EXISTS idx_source_categories_source ON source_categories(source_id);
CREATE INDEX IF NOT EXISTS idx_source_categories_category ON source_categories(category_id);

-- Embeddings for semantic search
CREATE TABLE IF NOT EXISTS embeddings (
    content_id TEXT PRIMARY KEY,
    embedding BLOB NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (content_id) REFERENCES content(id) ON DELETE CASCADE
);

-- Virtual table for vector similarity search
CREATE VIRTUAL TABLE IF NOT EXISTS vec_content USING vec0(
    content_id TEXT PRIMARY KEY,
    embedding FLOAT[384]
);

CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);

-- Triggers to update updated_at timestamps
CREATE TRIGGER IF NOT EXISTS update_sources_timestamp
AFTER UPDATE ON sources
BEGIN
    UPDATE sources SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_categories_timestamp
AFTER UPDATE ON categories
BEGIN
    UPDATE categories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_content_timestamp
AFTER UPDATE ON content
BEGIN
    UPDATE content SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;