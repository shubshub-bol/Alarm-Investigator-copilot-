"""Section-aware chunking.

Units, from largest to smallest: whole paragraphs (and tables) when they fit; otherwise list items and
sentences; only a single sentence longer than the limit is cut into word windows. Chunks are packed from
these units, and each chunk starts with the last whole sentence(s) of the previous chunk as overlap.
"""

import re

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_LIST_ITEM = re.compile(r"\n(?=\s*(?:\d+\.|[-*])\s)")
_SENTENCE_END = re.compile(r"(?<=[^\d\s][.!?])\s+(?=[A-Z0-9(])")  # not after "4." list numbers


def _windows(words: list[str], max_words: int, overlap: int) -> list[str]:
    step = max(1, max_words - overlap)
    out = []
    for start in range(0, len(words), step):
        out.append(" ".join(words[start : start + max_words]))
        if start + max_words >= len(words):
            break
    return out


def _units(paragraph: str) -> list[str]:
    """List items, then sentences inside them."""
    units = []
    for item in _LIST_ITEM.split(paragraph):
        units.extend(" ".join(s.split()) for s in _SENTENCE_END.split(item) if s.strip())
    return units


def _tail(text: str, overlap_words: int) -> str:
    """The trailing whole sentences of `text` that fit in overlap_words (possibly none)."""
    tail: list[str] = []
    for unit in reversed(_units(text)):
        if sum(len(u.split()) for u in tail) + len(unit.split()) > overlap_words:
            break
        tail.insert(0, unit)
    return " ".join(tail)


def chunk_section(text: str, max_words: int, overlap_words: int) -> list[str]:
    """Split one section (chunks never cross a section boundary) into chunks of at most max_words."""
    pieces: list[tuple[str, bool]] = []  # (text, starts a new paragraph)
    for paragraph in (p.strip() for p in _PARAGRAPH_BREAK.split(text)):
        if not paragraph:
            continue
        if len(paragraph.split()) <= max_words:
            pieces.append((paragraph, True))
            continue
        for i, unit in enumerate(_units(paragraph)):
            if len(unit.split()) <= max_words:
                pieces.append((unit, i == 0))
            else:
                pieces.extend(
                    (w, i == 0 and j == 0) for j, w in enumerate(_windows(unit.split(), max_words, overlap_words))
                )

    chunks: list[str] = []
    current: list[tuple[str, bool]] = []
    current_words = 0

    def flush() -> None:
        if current:
            text_out = current[0][0]
            for piece, new_paragraph in current[1:]:
                text_out += ("\n\n" if new_paragraph else "\n") + piece
            chunks.append(text_out)

    for piece, new_paragraph in pieces:
        n = len(piece.split())
        if current and current_words + n > max_words:
            flush()
            tail = _tail(chunks[-1], overlap_words) if overlap_words else ""
            current, current_words = [], 0
            if tail and len(tail.split()) + n <= max_words:
                current, current_words = [(tail, True)], len(tail.split())
        current.append((piece, new_paragraph))
        current_words += n
    flush()
    return chunks
