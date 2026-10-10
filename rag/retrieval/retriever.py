"""Hybrid retrieval: BM25 keywords + dense vectors, fused with Reciprocal Rank Fusion.

Why hybrid: alarm codes and part numbers ('PMP-VIB-HI', 'SP-BRG-6316') need exact keyword matching,
while operator questions ('the pump is shaking') need meaning-based matching. RRF combines the two
rankings without having to calibrate their very different score scales against each other.
"""

import logging
import re
import time
from collections import Counter
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from rag.config import RagSettings
from rag.embeddings import Embedder, default_embedder
from rag.index_store import LoadedIndex, load_index
from rag.models import Chunk
from rag.retrieval.citations import Citation, build_citation

log = logging.getLogger("rag.retrieval")

# Dense (cosine) score thresholds for bge-small-en-v1.5, set from measured scores on this corpus
# (see docs/rag-design.md). Relevant passages score ~0.70+; unrelated questions stay below ~0.55.
HIGH_CONFIDENCE = 0.70
MEDIUM_CONFIDENCE = 0.62
MIN_RELEVANCE = 0.55  # below this a passage is not used as evidence at all

# Small tie-breaking priors added to the RRF score, in units of 1/(rrf_k + 1) (one top rank is ~0.016;
# one rank step near the top is ~0.00026, so 0.1 is worth about six rank positions).
# Authority follows ALM-PHIL-006 section 7: approved procedures govern over other guidance.
AUTHORITY_BOOST = {
    "operating_procedure": 0.10,
    "safety_instruction": 0.10,
    "alarm_philosophy": 0.05,
    "maintenance_manual": 0.05,
    "troubleshooting_guide": 0.05,
}
ALARM_CODE_BOOST = 0.15  # passage is about an alarm code the caller is investigating

_WORD = re.compile(r"[a-z0-9]+")
_CODE = re.compile(r"\b[a-z]{2,4}(?:-[a-z0-9]{2,8}){1,3}\b")
_STOPWORDS = set(
    "a an and are as at be by can do does for from has have how i if in is it its of on or should "
    "that the their them then there this to was what when where which who why will with you your".split()
)


def tokenize(text: str) -> list[str]:
    """Words plus whole hyphenated codes, so 'PMP-VIB-HI' matches as one term as well as its parts."""
    lower = text.lower()
    return [w for w in _WORD.findall(lower) if w not in _STOPWORDS] + _CODE.findall(lower)


class RetrievalFilters(BaseModel):
    doc_types: list[str] | None = None
    equipment_types: list[str] | None = None
    doc_ids: list[str] | None = None
    site: str | None = Field(None, description="Keep documents for this site and site-wide ('all') documents")
    alarm_codes: list[str] | None = Field(None, description="Boost (not filter) passages about these alarm codes")
    include_quarantined: bool = False


class RetrievedPassage(BaseModel):
    rank: int
    chunk_id: str
    text: str
    score: float  # fused RRF score (+ alarm-code boost)
    dense_score: float  # cosine similarity, 0-1 in practice
    keyword_score: float  # BM25 normalised to the best keyword hit
    matched_terms: list[str]
    citation: Citation


class RetrievalResult(BaseModel):
    query: str
    filters: RetrievalFilters
    passages: list[RetrievedPassage]
    confidence: Literal["high", "medium", "low", "none"]
    top_dense_score: float | None
    quarantined_withheld: int
    message: str | None = None
    latency_ms: float


class Retriever:
    def __init__(self, index: LoadedIndex, embedder: Embedder, *, rrf_k: int = 60, per_doc_limit: int = 2):
        if index.manifest.get("embedder") != embedder.name:
            raise ValueError(
                f"Index was built with '{index.manifest.get('embedder')}' but the retriever uses '{embedder.name}'. "
                "Rebuild the index: python -m rag.ingestion"
            )
        self.chunks = index.chunks
        self.vectors = index.vectors
        self.embedder = embedder
        self.rrf_k = rrf_k
        self.per_doc_limit = per_doc_limit
        self._tokens = [set(tokenize(c.embed_text)) for c in self.chunks]
        self._bm25 = BM25Okapi([tokenize(c.embed_text) for c in self.chunks])

    @classmethod
    def from_settings(cls, settings: RagSettings, embedder: Embedder | None = None) -> "Retriever":
        embedder = embedder or default_embedder(settings.model_cache_dir, settings.embedding_model)
        return cls(load_index(settings.index_dir), embedder)

    # ----------------------------------------------------------------- filters
    @staticmethod
    def _passes(chunk: Chunk, f: RetrievalFilters) -> bool:
        if f.doc_types and chunk.doc_type not in f.doc_types:
            return False
        if f.doc_ids and chunk.doc_id not in f.doc_ids:
            return False
        if f.equipment_types and not set(f.equipment_types) & set(chunk.equipment_types):
            return chunk.equipment_types == []  # general documents (e.g. philosophy) always apply
        if f.site and chunk.site not in (f.site, "all"):
            return False
        return True

    # ------------------------------------------------------------------ search
    def search(self, query: str, k: int = 5, filters: RetrievalFilters | None = None) -> RetrievalResult:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        filters = filters or RetrievalFilters()
        started = time.perf_counter()

        eligible = [i for i, c in enumerate(self.chunks) if self._passes(c, filters)]
        withheld = sum(self.chunks[i].injection_flag for i in eligible) if not filters.include_quarantined else 0
        if not filters.include_quarantined:
            eligible = [i for i in eligible if not self.chunks[i].injection_flag]
        if not eligible:
            return self._result(query, filters, [], "none", None, withheld, started, "No documents match the filters.")

        idx = np.array(eligible)
        dense = self.vectors[idx] @ self.embedder.embed_query(query)
        keyword = np.asarray(self._bm25.get_scores(tokenize(query)))[idx]

        # Reciprocal Rank Fusion: each list contributes 1 / (k + rank). Keyword list only counts real hits.
        fused = np.zeros(len(idx))
        for rank, pos in enumerate(np.argsort(-dense), start=1):
            fused[pos] += 1 / (self.rrf_k + rank)
        for rank, pos in enumerate(np.argsort(-keyword), start=1):
            if keyword[pos] > 0:
                fused[pos] += 1 / (self.rrf_k + rank)
        unit = 1 / (self.rrf_k + 1)
        wanted = set(filters.alarm_codes or [])
        for pos, i in enumerate(idx):
            chunk = self.chunks[i]
            fused[pos] += AUTHORITY_BOOST.get(chunk.doc_type, 0.0) * unit
            if wanted & set(chunk.alarm_codes):
                fused[pos] += ALARM_CODE_BOOST * unit

        best_keyword = float(keyword.max()) if len(keyword) else 0.0
        q_terms = set(tokenize(query))
        picked: list[RetrievedPassage] = []
        per_doc: Counter = Counter()
        for pos in np.argsort(-fused):
            chunk = self.chunks[idx[pos]]
            if dense[pos] < MIN_RELEVANCE or per_doc[chunk.doc_id] >= self.per_doc_limit:
                continue
            per_doc[chunk.doc_id] += 1
            key = f"S{len(picked) + 1}"
            picked.append(
                RetrievedPassage(
                    rank=len(picked) + 1,
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    score=round(float(fused[pos]), 5),
                    dense_score=round(float(dense[pos]), 4),
                    keyword_score=round(float(keyword[pos]) / best_keyword, 3) if best_keyword else 0.0,
                    matched_terms=sorted(q_terms & self._tokens[idx[pos]]),
                    citation=build_citation(key, chunk),
                )
            )
            if len(picked) == k:
                break

        top_dense = float(dense.max())
        query_codes = set(_CODE.findall(query.lower()))
        exact_code_hit = bool(picked) and bool(query_codes & set(picked[0].matched_terms))
        if top_dense >= HIGH_CONFIDENCE:
            confidence = "high"
        elif top_dense >= MEDIUM_CONFIDENCE or exact_code_hit:  # a bare alarm code is a precise keyword query
            confidence = "medium"
        elif picked:
            confidence = "low"
        else:
            confidence = "none"
        message = None
        if confidence in ("low", "none"):
            message = "No sufficiently relevant passages found in the document corpus."
        return self._result(query, filters, picked, confidence, top_dense, withheld, started, message)

    @staticmethod
    def _result(query, filters, passages, confidence, top_dense, withheld, started, message) -> RetrievalResult:
        latency = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "retrieval",
            extra={
                "fields": {
                    "query": query[:200],
                    "filters": filters.model_dump(exclude_none=True),
                    "chunk_ids": [p.chunk_id for p in passages],
                    "scores": [p.dense_score for p in passages],
                    "confidence": confidence,
                    "quarantined_withheld": withheld,
                    "latency_ms": latency,
                }
            },
        )
        return RetrievalResult(
            query=query,
            filters=filters,
            passages=passages,
            confidence=confidence,
            top_dense_score=round(top_dense, 4) if top_dense is not None else None,
            quarantined_withheld=withheld,
            message=message,
            latency_ms=latency,
        )
