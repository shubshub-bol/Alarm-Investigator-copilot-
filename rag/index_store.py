"""The retrieval index on disk: chunks.jsonl (text + metadata), vectors.npy (embeddings), manifest.json."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from rag.models import Chunk

CHUNKS_FILE, VECTORS_FILE, MANIFEST_FILE = "chunks.jsonl", "vectors.npy", "manifest.json"


@dataclass
class LoadedIndex:
    chunks: list[Chunk]
    vectors: np.ndarray  # row i is the embedding of chunks[i]
    manifest: dict


def save_index(index_dir: Path, chunks: list[Chunk], vectors: np.ndarray, manifest: dict) -> None:
    if len(chunks) != len(vectors):
        raise ValueError("every chunk needs exactly one vector")
    index_dir.mkdir(parents=True, exist_ok=True)
    with (index_dir / CHUNKS_FILE).open("w", encoding="utf-8", newline="\n") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")
    np.save(index_dir / VECTORS_FILE, vectors.astype(np.float32))
    (index_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")


def load_manifest(index_dir: Path) -> dict | None:
    path = index_dir / MANIFEST_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_index(index_dir: Path) -> LoadedIndex:
    manifest = load_manifest(index_dir)
    if manifest is None:
        raise FileNotFoundError(f"No retrieval index in {index_dir}. Build it with: python -m rag.ingestion")
    with (index_dir / CHUNKS_FILE).open(encoding="utf-8") as f:
        chunks = [Chunk.model_validate_json(line) for line in f if line.strip()]
    return LoadedIndex(chunks, np.load(index_dir / VECTORS_FILE), manifest)
