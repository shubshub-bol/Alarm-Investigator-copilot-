"""Ingestion pipeline: load -> screen -> sanitise -> chunk -> label -> embed -> save, plus a staleness check."""

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rag.config import RagSettings
from rag.embeddings import Embedder
from rag.index_store import load_manifest, save_index
from rag.ingestion.chunker import chunk_section
from rag.ingestion.loaders import load_documents
from rag.ingestion.safety import sanitize, screen_for_injection
from rag.ingestion.tables import linearize_tables
from rag.models import Chunk, Document

log = logging.getLogger("rag.ingestion")

# Alarm codes look like PMP-VIB-HI or MTR-TRIP: letters only, ending in a condition word.
_CODE = re.compile(r"\b[A-Z]{2,4}(?:-[A-Z]{2,8}){1,3}\b")
_CODE_ENDINGS = {"HI", "LO", "LEAK", "TRIP", "SURGE", "FAIL", "DEV", "STUCK", "OVERLOAD"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "section"


def _codes_in(text: str) -> list[str]:
    return [c for c in _CODE.findall(text) if c.rsplit("-", 1)[-1] in _CODE_ENDINGS]


def build_chunks(doc: Document, max_words: int, overlap_words: int) -> list[Chunk]:
    chunks = []
    for s_idx, section in enumerate(doc.sections):
        flagged, reasons = screen_for_injection(section.text)  # raw text, so hidden comments are seen
        clean = linearize_tables(sanitize(section.text))
        if not clean:
            continue
        for i, piece in enumerate(chunk_section(clean, max_words, overlap_words)):
            codes = list(dict.fromkeys([*doc.alarm_codes, *_codes_in(piece)]))
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}::s{s_idx:02d}-{_slug(section.number or section.title)}::{i}",
                    doc_id=doc.doc_id,
                    title=doc.title,
                    doc_type=doc.doc_type,
                    site=doc.site,
                    revision=doc.revision,
                    effective_date=doc.effective_date,
                    equipment_types=doc.equipment_types,
                    alarm_codes=codes,
                    section_number=section.number,
                    section_title=section.title,
                    heading_path=section.heading_path,
                    source_path=doc.source_path,
                    chunk_index=i,
                    text=piece,
                    word_count=len(piece.split()),
                    content_hash=hashlib.sha256(piece.encode()).hexdigest()[:16],
                    injection_flag=flagged,
                    injection_reasons=reasons,
                )
            )
    return chunks


@dataclass
class IngestReport:
    documents: int
    chunks: int
    flagged_chunks: int
    embedder: str
    index_dir: Path
    duration_s: float
    per_document: list[dict] = field(default_factory=list)


def ingest(settings: RagSettings, embedder: Embedder) -> IngestReport:
    started = time.perf_counter()
    docs = load_documents(settings.document_path)
    chunks: list[Chunk] = []
    per_doc = []
    for doc in docs:
        doc_chunks = build_chunks(doc, settings.chunk_max_words, settings.chunk_overlap_words)
        chunks.extend(doc_chunks)
        flagged = sum(c.injection_flag for c in doc_chunks)
        per_doc.append(
            {
                "doc_id": doc.doc_id,
                "doc_type": doc.doc_type,
                "source_path": doc.source_path,
                "sha256": doc.sha256,
                "sections": len(doc.sections),
                "chunks": len(doc_chunks),
                "flagged_chunks": flagged,
            }
        )
        if flagged:
            log.warning("quarantined chunks", extra={"fields": {"doc_id": doc.doc_id, "flagged": flagged}})

    vectors = embedder.embed_documents([c.embed_text for c in chunks])
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "embedder": embedder.name,
        "dim": embedder.dim,
        "chunk_max_words": settings.chunk_max_words,
        "chunk_overlap_words": settings.chunk_overlap_words,
        "document_count": len(docs),
        "chunk_count": len(chunks),
        "flagged_chunks": sum(c.injection_flag for c in chunks),
        "documents": per_doc,
    }
    save_index(settings.index_dir, chunks, vectors, manifest)
    return IngestReport(
        documents=len(docs),
        chunks=len(chunks),
        flagged_chunks=manifest["flagged_chunks"],
        embedder=embedder.name,
        index_dir=settings.index_dir,
        duration_s=round(time.perf_counter() - started, 2),
        per_document=per_doc,
    )


@dataclass
class IndexStatus:
    up_to_date: bool
    reason: str
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def check_index(settings: RagSettings) -> IndexStatus:
    """Compare document fingerprints with the ones recorded at index time."""
    manifest = load_manifest(settings.index_dir)
    if manifest is None:
        return IndexStatus(False, "index not built")
    indexed = {d["doc_id"]: d["sha256"] for d in manifest["documents"]}
    current = {d.doc_id: d.sha256 for d in load_documents(settings.document_path)}
    status = IndexStatus(
        up_to_date=True,
        reason="up to date",
        changed=sorted(k for k in current.keys() & indexed.keys() if current[k] != indexed[k]),
        added=sorted(current.keys() - indexed.keys()),
        removed=sorted(indexed.keys() - current.keys()),
    )
    if status.changed or status.added or status.removed:
        status.up_to_date, status.reason = False, "documents changed since the index was built"
    return status
