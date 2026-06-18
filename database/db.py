"""Persistence layer for EzJudgements chat history.

Uses a threaded connection pool managed by the FastAPI lifespan.
All tables live under the `ezjudgements` schema — see schema.sql.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterable, Optional

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None


# --- Pool lifecycle -----------------------------------------------------------

def init_pool(database_url: str, minconn: int = 1, maxconn: int = 10) -> None:
    global _pool
    if _pool is not None:
        return
    _pool = ThreadedConnectionPool(minconn, maxconn, dsn=database_url)
    logger.info("db pool initialised min=%d max=%d", minconn, maxconn)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("db pool closed")


@contextmanager
def get_conn():
    if _pool is None:
        raise RuntimeError("db pool not initialised — call init_pool() first")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def ping() -> None:
    """Raise if the DB is unreachable. Used by the health endpoint."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()


# --- Conversations ------------------------------------------------------------

def create_conversation(user_identifier: Optional[str] = None,
                        title: Optional[str] = None) -> str:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ezjudgements.conversations (user_identifier, title)
            VALUES (%s, %s)
            RETURNING id
            """,
            (user_identifier, title),
        )
        return str(cur.fetchone()[0])


def set_conversation_title(conversation_id: str, title: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ezjudgements.conversations
               SET title = %s, updated_at = NOW()
             WHERE id = %s AND title IS NULL
            """,
            (title, conversation_id),
        )


def get_conversation(conversation_id: str) -> Optional[dict]:
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, user_identifier, title, created_at, updated_at
              FROM ezjudgements.conversations
             WHERE id = %s
            """,
            (conversation_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_conversations(user_identifier: Optional[str] = None,
                       limit: int = 50,
                       offset: int = 0) -> list[dict]:
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        where = ""
        params: list = []
        if user_identifier is not None:
            where = "WHERE c.user_identifier = %s"
            params.append(user_identifier)
        params.extend([limit, offset])
        cur.execute(
            f"""
            SELECT c.id, c.title, c.user_identifier, c.created_at, c.updated_at,
                   COALESCE(m.cnt, 0) AS message_count
              FROM ezjudgements.conversations c
         LEFT JOIN (
                   SELECT conversation_id, COUNT(*) AS cnt
                     FROM ezjudgements.messages
                    GROUP BY conversation_id
                   ) m ON m.conversation_id = c.id
             {where}
          ORDER BY c.updated_at DESC
             LIMIT %s OFFSET %s
            """,
            params,
        )
        return [dict(r) for r in cur.fetchall()]


def delete_conversation(conversation_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ezjudgements.conversations WHERE id = %s",
            (conversation_id,),
        )
        return cur.rowcount > 0


# --- Messages -----------------------------------------------------------------

def save_message(conversation_id: str,
                 role: str,
                 content: str,
                 openai_response_id: Optional[str] = None) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ezjudgements.messages
                (conversation_id, role, content, openai_response_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (conversation_id, role, content, openai_response_id),
        )
        message_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE ezjudgements.conversations SET updated_at = NOW() WHERE id = %s",
            (conversation_id,),
        )
        return message_id


def load_conversation_messages(conversation_id: str) -> list[dict]:
    """Return messages for a conversation in chronological order, with metadata."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, role, content, created_at
              FROM ezjudgements.messages
             WHERE conversation_id = %s
             ORDER BY id ASC
            """,
            (conversation_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def load_history_for_model(conversation_id: str) -> list[dict]:
    """Minimal shape the OpenAI Responses API wants for `input`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT role, content
              FROM ezjudgements.messages
             WHERE conversation_id = %s
             ORDER BY id ASC
            """,
            (conversation_id,),
        )
        return [{"role": role, "content": content} for role, content in cur.fetchall()]


# --- Tool calls & citations ---------------------------------------------------

def save_tool_call(message_id: int,
                   tool_type: str,
                   queries: Optional[list] = None,
                   result_count: Optional[int] = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ezjudgements.tool_calls
                (message_id, tool_type, queries, result_count)
            VALUES (%s, %s, %s, %s)
            """,
            (message_id, tool_type, Json(queries) if queries is not None else None, result_count),
        )


def save_citations(message_id: int, citations: Iterable[tuple[str, Optional[str]]]) -> None:
    rows = [(message_id, file_id, filename) for file_id, filename in citations]
    if not rows:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ezjudgements.citations (message_id, file_id, filename)
            VALUES (%s, %s, %s)
            """,
            rows,
        )
