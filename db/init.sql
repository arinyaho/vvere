CREATE TABLE IF NOT EXISTS repos (
    id SERIAL PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT GENERATED ALWAYS AS (owner || '/' || name) STORED,
    UNIQUE (owner, name)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id BIGINT PRIMARY KEY,
    repo_id INT NOT NULL REFERENCES repos(id),
    workflow_id BIGINT NOT NULL,
    workflow_name TEXT NOT NULL,
    trigger TEXT NOT NULL,          -- push / pull_request / schedule / workflow_dispatch
    branch TEXT,
    status TEXT,                    -- completed / in_progress / queued
    conclusion TEXT,                -- success / failure / cancelled / skipped / timed_out
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    duration_seconds INT,
    run_url TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_repo_trigger ON workflow_runs (repo_id, trigger, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_created ON workflow_runs (created_at DESC);

CREATE TABLE IF NOT EXISTS pull_requests (
    id BIGINT PRIMARY KEY,
    repo_id INT NOT NULL REFERENCES repos(id),
    number INT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    is_automated BOOLEAN NOT NULL DEFAULT FALSE,
    state TEXT NOT NULL,            -- open / closed
    is_merged BOOLEAN NOT NULL DEFAULT FALSE,
    draft BOOLEAN NOT NULL DEFAULT FALSE,
    head_branch TEXT,
    base_branch TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    merged_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    pr_url TEXT NOT NULL,
    requested_reviewers JSONB DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_prs_repo_state ON pull_requests (repo_id, state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_prs_updated ON pull_requests (updated_at DESC);

CREATE TABLE IF NOT EXISTS branches (
    id SERIAL PRIMARY KEY,
    repo_id INT NOT NULL REFERENCES repos(id),
    name TEXT NOT NULL,
    last_commit_at TIMESTAMPTZ,
    last_commit_sha TEXT,
    is_staging BOOLEAN NOT NULL DEFAULT FALSE,  -- stage/* prefix
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (repo_id, name)
);

-- Incremental fetch cursors
CREATE TABLE IF NOT EXISTS fetch_cursor (
    repo_id INT NOT NULL REFERENCES repos(id),
    data_type TEXT NOT NULL,        -- runs / prs / branches
    last_fetched_at TIMESTAMPTZ,
    last_seen_id BIGINT,
    PRIMARY KEY (repo_id, data_type)
);

-- Additional indexes for snapshot/dashboard query performance
CREATE INDEX IF NOT EXISTS idx_prs_repo_merged ON pull_requests (repo_id, merged_at) WHERE merged_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_branches_repo_commit ON branches (repo_id, last_commit_at);
CREATE INDEX IF NOT EXISTS idx_runs_repo_branch ON workflow_runs (repo_id, branch, created_at DESC);

-- Collector sync health tracking
CREATE TABLE IF NOT EXISTS sync_status (
    id INT PRIMARY KEY DEFAULT 1,
    last_sync_at TIMESTAMPTZ,
    last_sync_ok BOOLEAN,
    last_error TEXT,
    CHECK (id = 1)  -- singleton row
);
INSERT INTO sync_status (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Admin setup state (wizard token, config)
CREATE TABLE IF NOT EXISTS admin_config (
    id INT PRIMARY KEY DEFAULT 1,
    github_token TEXT,              -- encrypted or plain (for self-hosted)
    github_org TEXT,
    initial_fetch_days INT DEFAULT 90,
    setup_complete BOOLEAN NOT NULL DEFAULT FALSE,
    setup_by TEXT,                   -- GitHub username of admin
    setup_at TIMESTAMPTZ,
    CHECK (id = 1)  -- singleton row
);
INSERT INTO admin_config (id) VALUES (1) ON CONFLICT DO NOTHING;

-- LLM recommendation cache (singleton)
CREATE TABLE IF NOT EXISTS recommendations_cache (
    id INT PRIMARY KEY DEFAULT 1,
    recommendation TEXT,            -- JSON blob
    generated_at TIMESTAMPTZ,
    CHECK (id = 1)
);

-- Repos are seeded from the REPOS env var by the collector on startup.
-- No hardcoded repos here.
