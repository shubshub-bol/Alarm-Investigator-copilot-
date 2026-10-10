"""Section-aware chunking: pack whole paragraphs up to max_words; split only paragraphs that are too long."""

import re

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _windows(words: list[str], max_words: int, overlap: int) -> list[str]:
    step = max(1, max_words - overlap)
    out = []
    for start in range(0, len(words), step):
        out.append(" ".join(words[start : start + max_words]))
        if start + max_words >= len(words):
            break
    return out


def chunk_section(text: str, max_words: int, overlap_words: int) -> list[str]:
    """Chunks never cross a section boundary (the caller passes one section at a time).

    Paragraphs (and tables, which have no blank lines inside) stay whole when they fit. The next
    chunk starts with the last `overlap_words` words of the previous one to keep context.
    """
    chunks: list[str] = []
    current: list[str] = []  # paragraphs in the chunk being built
    current_words = 0

    def flush() -> None:
        nonlocal current, current_words
        if current:
            chunks.append("\n\n".join(current))
        current, current_words = [], 0

    for paragraph in (p.strip() for p in _PARAGRAPH_BREAK.split(text)):
        if not paragraph:
            continue
        words = paragraph.split()
        if len(words) > max_words:  # too long on its own: overlapping word windows
            flush()
            chunks.extend(_windows(words, max_words, overlap_words))
            continue
        if current_words + len(words) > max_words:
            previous_tail = " ".join(chunks[-1].split()[-overlap_words:]) if chunks and overlap_words else ""
            flush()
            if previous_tail and len(previous_tail.split()) + len(words) <= max_words:
                current, current_words = [previous_tail], len(previous_tail.split())
        current.append(paragraph)
        current_words += len(words)
    flush()
    return chunks
