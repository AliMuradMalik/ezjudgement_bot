"""Agentic RAG service wrapping the OpenAI Responses API + file_search tool."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from openai import AsyncOpenAI

from app.sources import document_urls
from prompts.ezprompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# OpenAI embeds inline file-citation markers in the answer text, wrapped in
# private-use sentinel characters (U+E200 ... U+E201) around an ASCII token
# like "citeturn0file0". They are metadata, not content — the readable
# citation the model writes stays in the prose, and the cited files are listed
# separately under "citations". Strip the markers so they never reach the UI.
_CITE_OPEN = ""
_CITE_CLOSE = ""
_CITE_SPAN_RE = re.compile("[\\s\\S]*?")
# Any stray private-use sentinels left dangling (e.g. unterminated span).
_CITE_STRAY_RE = re.compile("[-]")


def _strip_citation_markers(text: str) -> str:
    """Remove OpenAI inline citation marker spans from a completed string."""
    if not text:
        return text
    text = _CITE_SPAN_RE.sub("", text)
    return _CITE_STRAY_RE.sub("", text)


# Internal corpus filenames (e.g. "CLC1994K216", "SCMR2013S140", "2013SCMR140")
# are NOT legal citations, but the model occasionally leaks them into the
# answer despite the system prompt. Scrub them as a hard guarantee. Real
# reported citations ("1994 CLC 216", "2013 SCMR 140") put the year FIRST with
# spaces, so they never match these shapes.
_FILENAME_TOKEN_RE = re.compile(
    r"\b(?:"
    r"(?:CLC|SCMR)[\s-]?\d{4}[\s-]?[A-Z][\s-]?\d{1,5}"  # CLC1994K216 / CLC 1994 K 216
    r"|\d{4}(?:CLC|SCMR)\d{1,5}"                        # 2013SCMR140 (compact only)
    r")(?:\.pdf)?\b",
    re.IGNORECASE,
)
_FILENAME_PLACEHOLDER = "(see source documents below)"


def _replace_filename_tokens(text: str) -> str:
    """Swap leaked internal filename tokens for a pointer to the Sources list."""
    if not text:
        return text
    text = _FILENAME_TOKEN_RE.sub(_FILENAME_PLACEHOLDER, text)
    # Tidy the common "(CLC1994K216)" case, which becomes double-wrapped.
    return text.replace(f"({_FILENAME_PLACEHOLDER})", _FILENAME_PLACEHOLDER)


def _sanitize_answer(text: str) -> str:
    """Full cleanup for a completed answer string."""
    return _replace_filename_tokens(_strip_citation_markers(text))


class _DeltaSanitizer:
    """Strip citation marker spans across streamed deltas.

    A marker span can be split over several `text.delta` chunks, so we buffer
    from an opening sentinel until its closing sentinel arrives, emitting only
    the clean text in between events.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> str:
        self._buf += delta
        out: list[str] = []
        while True:
            open_idx = self._buf.find(_CITE_OPEN)
            if open_idx == -1:
                out.append(self._buf)
                self._buf = ""
                break
            out.append(self._buf[:open_idx])
            close_idx = self._buf.find(_CITE_CLOSE, open_idx + 1)
            if close_idx == -1:
                # Span not finished yet — hold it back until more arrives.
                self._buf = self._buf[open_idx:]
                break
            self._buf = self._buf[close_idx + 1:]
        return "".join(out)

    def flush(self) -> str:
        rest = _strip_citation_markers(self._buf)
        self._buf = ""
        return rest


class _FilenameSanitizer:
    """Replace leaked filename tokens across streamed deltas.

    A token like "CLC1994K216" can be split over several chunks, so we always
    hold back a short tail (longer than any token) and only emit text that can
    no longer be the start of one.
    """

    HOLDBACK = 24  # > longest token incl. spaces and ".pdf"

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        self._buf += delta
        if len(self._buf) <= self.HOLDBACK:
            return ""
        cut = len(self._buf) - self.HOLDBACK
        # Never cut mid-token: a token straddling the boundary stays buffered
        # whole, so substitution only ever sees completed tokens.
        for match in _FILENAME_TOKEN_RE.finditer(self._buf):
            if match.start() < cut < match.end():
                cut = match.start()
                break
        out = _replace_filename_tokens(self._buf[:cut])
        self._buf = self._buf[cut:]
        return out

    def flush(self) -> str:
        rest = _replace_filename_tokens(self._buf)
        self._buf = ""
        return rest


@dataclass
class ToolCallRecord:
    tool_type: str
    queries: list[str] = field(default_factory=list)
    result_count: int | None = None


@dataclass
class CitationRecord:
    file_id: str
    filename: str | None = None
    judgment_url: str | None = None  # link to the full judgment PDF, if on disk
    headnote_url: str | None = None  # link to the headnote PDF, if on disk
    url: str | None = None           # web page URL (web_search citations)
    title: str | None = None         # web page title (web_search citations)


@dataclass
class AgentResult:
    response_id: str
    answer: str
    tool_calls: list[ToolCallRecord]
    citations: list[CitationRecord]


class RagService:
    """Agentic file-search RAG over a single OpenAI vector store."""

    def __init__(self, api_key: str, vector_store_id: str, model: str, max_num_results: int):
        self._client = AsyncOpenAI(api_key=api_key)
        self._vector_store_id = vector_store_id
        self._model = model
        self._max_num_results = max_num_results

    # ---- plumbing ------------------------------------------------------------

    def _tools(self) -> list[dict]:
        return [
            {
                "type": "file_search",
                "vector_store_ids": [self._vector_store_id],
                "max_num_results": self._max_num_results,
            },
            # Fallback when the corpus lacks the judgement — the system prompt
            # instructs the model to try file_search first.
            {"type": "web_search"},
        ]

    # ---- non-streaming -------------------------------------------------------

    async def answer(self, history: list[dict]) -> AgentResult:
        response = await self._client.responses.create(
            model=self._model,
            instructions=SYSTEM_PROMPT,
            input=history,
            tools=self._tools(),
            tool_choice="auto",
            include=["file_search_call.results"],
        )
        return AgentResult(
            response_id=response.id,
            answer=_sanitize_answer(response.output_text),
            tool_calls=_extract_tool_calls(response),
            citations=_extract_citations(response),
        )

    # ---- streaming (SSE) -----------------------------------------------------

    async def answer_stream(self, history: list[dict]) -> AsyncIterator[dict]:
        """Yield semantic events for SSE: tool searches, text deltas, citations, done."""
        logger.info("stream start model=%s history_turns=%d", self._model, len(history))

        try:
            stream = await self._client.responses.create(
                model=self._model,
                instructions=SYSTEM_PROMPT,
                input=history,
                tools=self._tools(),
                tool_choice="auto",
                stream=True,
                include=["file_search_call.results"],
            )
        except Exception:
            logger.exception("responses.create failed")
            yield {"type": "error", "message": "upstream model error"}
            return

        final_answer_parts: list[str] = []
        final_response = None
        event_counts: dict[str, int] = {}
        sanitizer = _DeltaSanitizer()
        fname_sanitizer = _FilenameSanitizer()

        try:
            async for event in stream:
                etype = getattr(event, "type", "") or ""
                event_counts[etype] = event_counts.get(etype, 0) + 1
                logger.debug("stream event type=%s", etype)

                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        clean = fname_sanitizer.feed(sanitizer.feed(delta))
                        if clean:
                            final_answer_parts.append(clean)
                            yield {"type": "text.delta", "delta": clean}

                elif etype == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item is not None and getattr(item, "type", "") in ("file_search_call", "web_search_call"):
                        yield {"type": "tool.searching"}

                elif etype == "response.output_item.done":
                    item = getattr(event, "item", None)
                    itype = getattr(item, "type", "") if item is not None else ""
                    if itype == "file_search_call":
                        yield {
                            "type": "tool.searched",
                            "queries": list(getattr(item, "queries", None) or []),
                        }
                    elif itype == "web_search_call":
                        yield {
                            "type": "tool.searched",
                            "queries": _web_search_queries(item),
                        }

                elif etype.startswith(("response.file_search_call.", "response.web_search_call.")) \
                        and etype.endswith((".in_progress", ".searching")):
                    yield {"type": "tool.searching"}

                elif etype == "response.completed":
                    final_response = getattr(event, "response", None)

                elif etype in ("error", "response.failed", "response.incomplete"):
                    err_obj = (getattr(event, "error", None)
                               or getattr(event, "response", None)
                               or event)
                    logger.error("stream error event type=%s payload=%r", etype, err_obj)
                    yield {"type": "error", "message": "upstream model error"}
                    return
        except Exception:
            logger.exception("stream iteration failed")
            yield {"type": "error", "message": "upstream model error"}
            return

        # Emit any text held back while waiting for a citation span to close
        # or for a possible filename token to complete.
        tail = fname_sanitizer.feed(sanitizer.flush()) + fname_sanitizer.flush()
        if tail:
            final_answer_parts.append(tail)
            yield {"type": "text.delta", "delta": tail}

        answer = "".join(final_answer_parts)
        logger.info("stream done events=%s chars=%d", event_counts, len(answer))

        # Fallback: no streamed deltas arrived but we do have a final response.
        # Extract the completed message text so the user isn't stuck with a
        # blank bubble even if the SDK version doesn't emit text deltas.
        if not answer and final_response is not None:
            answer = _sanitize_answer(getattr(final_response, "output_text", "") or "")
            if answer:
                yield {"type": "text.delta", "delta": answer}

        if not answer:
            yield {
                "type": "error",
                "message": (
                    "no text received from model. event counts: "
                    + ", ".join(f"{k}={v}" for k, v in event_counts.items())
                ),
            }

        tool_calls: list[ToolCallRecord] = []
        citations: list[CitationRecord] = []
        response_id = ""

        if final_response is not None:
            response_id = getattr(final_response, "id", "") or ""
            tool_calls = _extract_tool_calls(final_response)
            citations = _extract_citations(final_response)

        yield {
            "type": "done",
            "response_id": response_id,
            "answer": answer,
            "tool_calls": [tc.__dict__ for tc in tool_calls],
            "citations": [c.__dict__ for c in citations],
        }


# ---- extraction helpers (work on a completed response object) -----------------

def _web_search_queries(item) -> list[str]:
    """Query string(s) from a web_search_call item, tolerant of SDK shape."""
    action = getattr(item, "action", None)
    query = getattr(action, "query", None) if action is not None else None
    return [query] if query else []


def _extract_tool_calls(response) -> list[ToolCallRecord]:
    calls: list[ToolCallRecord] = []
    for item in getattr(response, "output", None) or []:
        itype = getattr(item, "type", "")
        if itype == "file_search_call":
            queries = list(getattr(item, "queries", None) or [])
            results = getattr(item, "results", None)
            calls.append(ToolCallRecord(
                tool_type="file_search",
                queries=queries,
                result_count=len(results) if results else None,
            ))
        elif itype == "web_search_call":
            calls.append(ToolCallRecord(
                tool_type="web_search",
                queries=_web_search_queries(item),
            ))
    return calls


def _records_from(seen: dict[str, str | None]) -> list[CitationRecord]:
    """Turn {file_id: filename} into CitationRecords with PDF links attached."""
    records: list[CitationRecord] = []
    for fid, name in seen.items():
        urls = document_urls(name)
        records.append(CitationRecord(
            file_id=fid,
            filename=name,
            judgment_url=urls["judgment"],
            headnote_url=urls["headnote"],
        ))
    return records


def _citations_from_annotations(response) -> dict[str, str | None]:
    """Files the model explicitly cited, via output_text `file_citation` anns."""
    seen: dict[str, str | None] = {}
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) != "output_text":
                continue
            for ann in getattr(part, "annotations", None) or []:
                if getattr(ann, "type", None) == "file_citation":
                    fid = getattr(ann, "file_id", None)
                    if fid and fid not in seen:
                        seen[fid] = getattr(ann, "filename", None)
    return seen


def _files_from_search(response) -> dict[str, str | None]:
    """Unique files file_search retrieved (requires include=file_search_call.results).

    Fallback for Sources when the model emits inline citation markers but no
    structured `file_citation` annotations — keeps the Sources list populated.
    """
    seen: dict[str, str | None] = {}
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "file_search_call":
            continue
        for r in getattr(item, "results", None) or []:
            fid = getattr(r, "file_id", None)
            if fid and fid not in seen:
                seen[fid] = getattr(r, "filename", None)
    return seen


def _web_citations(response) -> list[CitationRecord]:
    """Web pages the model cited, via output_text `url_citation` annotations.

    Emitted by the web_search tool. The URL doubles as the citation's file_id
    so persistence (file_id, filename) keeps working unchanged; the page
    title is stored as the filename.
    """
    seen: dict[str, str | None] = {}
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) != "output_text":
                continue
            for ann in getattr(part, "annotations", None) or []:
                if getattr(ann, "type", None) == "url_citation":
                    url = getattr(ann, "url", None)
                    if url and url not in seen:
                        seen[url] = getattr(ann, "title", None)
    return [
        CitationRecord(file_id=url, filename=title, url=url, title=title)
        for url, title in seen.items()
    ]


def _extract_citations(response) -> list[CitationRecord]:
    # Prefer the files / web pages the model actually cited; only when the API
    # attached no structured citations at all, fall back to the files the
    # vector-store search retrieved.
    file_seen = _citations_from_annotations(response)
    web_records = _web_citations(response)
    if not file_seen and not web_records:
        file_seen = _files_from_search(response)
    return _records_from(file_seen) + web_records
