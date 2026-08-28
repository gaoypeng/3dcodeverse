"""Deterministic context compaction for the api-agent loop (no LLM involved).

Old tool-result bodies are trimmed to a stub, old images are dropped, and a
"facts so far" note is inserted after the first user message so the model keeps
the task + a running summary while the transcript on disk stays complete.
"""

from __future__ import annotations

from codeverse.contracts.chat import ChatMessage, ImagePart, TextPart, ToolCallPart, ToolResultPart

STUB_CHARS = 240
FACTS_TAG = "<facts_so_far>"
#: tool-call arguments whose payload is a file body: once written, the file is on disk
#: and ``read_file`` can fetch it, so keeping the text in the conversation buys nothing
#: and is re-processed on every later turn.  Measured 2026-08-27 on one assemble
#: session: 29 write_file calls were 49 % of the whole conversation.
BODY_ARG_TOOLS = frozenset({"write_file", "edit_file"})
BODY_ARG_KEYS = ("content", "new", "old", "text")


def message_chars(messages: list[ChatMessage]) -> int:
    """Rough context size: text chars + a flat 4k per image."""
    n = 0
    for m in messages:
        for p in m.parts:
            if isinstance(p, TextPart):
                n += len(p.text)
            elif isinstance(p, ToolResultPart):
                n += len(p.content) + 4000 * len(p.images)
            elif isinstance(p, ImagePart):
                n += 4000
            else:  # tool call
                n += len(str(getattr(p, "arguments", "")))
    return n


def _stub(content: str) -> str:
    if len(content) <= STUB_CHARS:
        return content
    return content[:STUB_CHARS] + f" … [compacted; {len(content)} chars originally]"


def _stub_written_bodies(m: ChatMessage) -> ChatMessage:
    """Shorten the file bodies carried in this message's write/edit tool CALLS."""
    if not any(isinstance(p, ToolCallPart) and p.name in BODY_ARG_TOOLS for p in m.parts):
        return m
    parts: list[object] = []
    for p in m.parts:
        if isinstance(p, ToolCallPart) and p.name in BODY_ARG_TOOLS:
            args = dict(p.arguments or {})
            for k in BODY_ARG_KEYS:
                if isinstance(args.get(k), str):
                    args[k] = _stub(args[k])
            parts.append(ToolCallPart(id=p.id, name=p.name, arguments=args))
        else:
            parts.append(p)
    return ChatMessage(role=m.role, parts=parts)  # type: ignore[arg-type]


def compact_messages(messages: list[ChatMessage], *, keep_recent: int = 8, facts: str = "") -> list[ChatMessage]:
    """Return a new message list with old bodies stubbed and a facts note.

    The first message (the task) and the last ``keep_recent`` messages are kept
    verbatim; everything in between has tool-result bodies shortened, images removed,
    and the file bodies inside old ``write_file``/``edit_file`` CALLS stubbed — the
    file is on disk, so re-sending its text every turn buys nothing.  Any earlier
    facts note is replaced.
    """
    if len(messages) <= keep_recent + 1:
        return list(messages)
    head, middle, tail = messages[0], messages[1:-keep_recent], messages[-keep_recent:]
    out: list[ChatMessage] = [head]
    if facts:
        out.append(ChatMessage.user(f"{FACTS_TAG}\n{facts}\n</facts_so_far>"))
    for m in middle:
        if m.role == "user" and m.text.startswith(FACTS_TAG):
            continue  # superseded
        if m.role != "tool":
            out.append(_stub_written_bodies(m))
            continue
        parts = [
            ToolResultPart(call_id=p.call_id, name=p.name, content=_stub(p.content), images=[], is_error=p.is_error)
            if isinstance(p, ToolResultPart) else p
            for p in m.parts
        ]
        out.append(ChatMessage(role="tool", parts=parts))
    out.extend(tail)
    return out


__all__ = ["compact_messages", "message_chars", "FACTS_TAG", "BODY_ARG_TOOLS"]
