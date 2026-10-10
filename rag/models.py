"""Records that flow through ingestion and retrieval."""

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class Section:
    """A heading and the raw text under it, before cleaning and chunking."""

    number: str | None  # "4.1" for "4.1 High vibration", None when the heading has no number
    title: str
    heading_path: list[str]  # document title -> parent headings -> this heading
    text: str


@dataclass
class Document:
    doc_id: str
    title: str
    doc_type: str
    source_path: str  # relative to the corpus folder
    sha256: str  # fingerprint of the file bytes; drives index staleness checks
    equipment_types: list[str] = field(default_factory=list)
    alarm_codes: list[str] = field(default_factory=list)
    site: str = "all"
    revision: str = "unknown"
    effective_date: str | None = None
    owner: str | None = None
    sections: list[Section] = field(default_factory=list)


class Chunk(BaseModel):
    """One retrievable passage plus everything needed to filter, rank and cite it."""

    chunk_id: str
    doc_id: str
    title: str
    doc_type: str
    site: str
    revision: str
    effective_date: str | None
    equipment_types: list[str]
    alarm_codes: list[str]
    section_number: str | None
    section_title: str
    heading_path: list[str]
    source_path: str
    chunk_index: int
    text: str
    word_count: int
    content_hash: str
    injection_flag: bool = False
    injection_reasons: list[str] = []

    @property
    def citation_label(self) -> str:
        return (
            f"{self.doc_id} §{self.section_number}" if self.section_number else f"{self.doc_id} › {self.section_title}"
        )

    @property
    def embed_text(self) -> str:
        """Text sent to the embedder: the passage prefixed with where it comes from."""
        return f"{self.title}\n{' > '.join(self.heading_path[1:])}\n{self.text}"
