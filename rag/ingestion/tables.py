"""Markdown tables -> one sentence per row.

'| P1 | ... | 5 minutes |' embeds poorly because the meaning lives in the header row. Writing each row as
'Priority: P1; Response required within: 5 minutes.' keeps the header next to every value, which helps
keyword search, embeddings and the model that reads the passage.
"""

import re

_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def linearize_tables(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        is_table = _ROW.match(lines[i]) and i + 1 < len(lines) and _SEPARATOR.match(lines[i + 1])
        if not is_table:
            out.append(lines[i])
            i += 1
            continue
        headers = _cells(lines[i])
        i += 2
        while i < len(lines) and _ROW.match(lines[i]):
            pairs = [f"{h}: {v}" for h, v in zip(headers, _cells(lines[i]), strict=False) if v and v != "-"]
            out.append("; ".join(pairs) + ".")
            i += 1
    return "\n".join(out)
