"""Text extraction: Markdown, plain text and PDF files -> Document with metadata and sections."""

import hashlib
import logging
import re
from pathlib import Path

from pypdf import PdfReader

from rag.models import Document, Section

log = logging.getLogger("rag.ingestion")

SUPPORTED = {".md", ".txt", ".pdf"}
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Read a '---' delimited block of 'key: value' lines. '[a, b]' becomes a list."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text
    meta: dict = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n")


def _section_number(title: str) -> str | None:
    match = _NUMBER.match(title)
    return match.group(1) if match else None


def markdown_sections(body: str, doc_title: str) -> list[Section]:
    """Split on headings. Each section keeps the full heading path so a chunk knows where it sits."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []  # (level, heading) of the current path
    current: list[str] = []

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            title = stack[-1][1] if stack else doc_title
            path = [h for _, h in stack] or [doc_title]
            if path[0] != doc_title:
                path = [doc_title, *path]
            sections.append(Section(_section_number(title), title, path, text))
        current.clear()

    for line in body.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2)))
        else:
            current.append(line)
    flush()
    return sections


def _is_text_heading(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 8 and stripped == stripped.upper() and any(c.isalpha() for c in stripped)


def text_sections(body: str, doc_title: str) -> list[Section]:
    """Plain text: a line written fully in capitals starts a new section."""
    sections: list[Section] = []
    title, current = doc_title, []
    for line in body.splitlines():
        if _is_text_heading(line):
            if "\n".join(current).strip():
                sections.append(Section(None, title, [doc_title, title], "\n".join(current).strip()))
            title, current = line.strip(), []
        else:
            current.append(line)
    if "\n".join(current).strip():
        sections.append(Section(None, title, [doc_title, title], "\n".join(current).strip()))
    return sections


def read_pdf_text(path: Path) -> str:
    return "\n\n".join((page.extract_text() or "") for page in PdfReader(path).pages).strip()


def load_document(path: Path, root: Path) -> Document:
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        meta, body = {}, read_pdf_text(path)
    else:
        meta, body = parse_front_matter(raw.decode("utf-8"))

    stem_id, _, stem_rest = path.stem.partition("_")
    title = meta.get("title") or stem_rest.replace("_", " ").strip().title() or stem_id
    if suffix == ".md":
        sections = markdown_sections(body, title)
    elif suffix == ".txt":
        sections = text_sections(body, title)
    else:
        sections = [Section(None, title, [title], body)] if body else []

    return Document(
        doc_id=meta.get("doc_id") or stem_id,
        title=title,
        doc_type=meta.get("doc_type") or "unclassified",
        source_path=path.relative_to(root).as_posix(),
        sha256=hashlib.sha256(raw).hexdigest(),
        equipment_types=meta.get("equipment_types") or [],
        alarm_codes=meta.get("alarm_codes") or [],
        site=meta.get("site") or "all",
        revision=meta.get("revision") or "unknown",
        effective_date=meta.get("effective_date"),
        owner=meta.get("owner"),
        sections=sections,
    )


def load_documents(folder: Path) -> list[Document]:
    folder = Path(folder)
    docs = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED:
            log.info("skipping unsupported file", extra={"fields": {"path": path.name}})
            continue
        docs.append(load_document(path, folder))
    return docs
