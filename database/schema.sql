-- EzJudgements chat-history schema.
-- Isolated in its own schema so it cannot collide with existing production tables.
-- Idempotent: safe to re-run. Does not DROP, ALTER, or touch anything outside this schema.

CREATE SCHEMA IF NOT EXISTS ezjudgements;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- One row per chat session.
CREATE TABLE IF NOT EXISTS ezjudgements.conversations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_identifier   TEXT,                            -- email / external user id, optional
    title             TEXT,                            -- auto-filled from first user message
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_identifier
    ON ezjudgements.conversations (user_identifier);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
    ON ezjudgements.conversations (updated_at DESC);

-- Every user / assistant / system turn.
CREATE TABLE IF NOT EXISTS ezjudgements.messages (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     UUID NOT NULL
                        REFERENCES ezjudgements.conversations(id) ON DELETE CASCADE,
    role                TEXT NOT NULL
                        CHECK (role IN ('user', 'assistant', 'system')),
    content             TEXT NOT NULL,
    openai_response_id  TEXT,                          -- Responses API id, for audit / replay
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
    ON ezjudgements.messages (conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at
    ON ezjudgements.messages (created_at);

-- Every file_search (or future tool) invocation, so you can see what the agent searched for.
CREATE TABLE IF NOT EXISTS ezjudgements.tool_calls (
    id            BIGSERIAL PRIMARY KEY,
    message_id    BIGINT NOT NULL
                  REFERENCES ezjudgements.messages(id) ON DELETE CASCADE,
    tool_type     TEXT NOT NULL,                       -- 'file_search', etc.
    queries       JSONB,                               -- array of query strings the model issued
    result_count  INT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_message_id
    ON ezjudgements.tool_calls (message_id);

-- Every judgement/PDF chunk the model cited in its answer.
CREATE TABLE IF NOT EXISTS ezjudgements.citations (
    id          BIGSERIAL PRIMARY KEY,
    message_id  BIGINT NOT NULL
                REFERENCES ezjudgements.messages(id) ON DELETE CASCADE,
    file_id     TEXT NOT NULL,                         -- OpenAI file id
    filename    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_citations_message_id
    ON ezjudgements.citations (message_id);
CREATE INDEX IF NOT EXISTS idx_citations_file_id
    ON ezjudgements.citations (file_id);
