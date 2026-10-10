"""Embedders. Both return L2-normalised float32 vectors, so cosine similarity is a dot product.

- OnnxBgeEmbedder: BAAI/bge-small-en-v1.5 run locally with onnxruntime + tokenizers (no API key,
  no network after the first download, deterministic).
- HashingEmbedder: hashed bag-of-words; no model at all. Used by fast tests and as a last-resort fallback.
"""

import hashlib
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger("rag.embeddings")


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return (matrix / np.where(norms == 0, 1, norms)).astype(np.float32)


class HashingEmbedder:
    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _vector(self, text: str) -> np.ndarray:
        words = re.findall(r"[a-z0-9]+", text.lower())
        features = words + [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)]
        vec = np.zeros(self.dim, dtype=np.float32)
        for feature in features:
            h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        return vec

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return _normalise(np.stack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class OnnxBgeEmbedder:
    """bge-small-en-v1.5: CLS-token pooling + L2 normalisation, as the model card specifies."""

    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(
        self,
        cache_dir: Path,
        model_repo: str = "BAAI/bge-small-en-v1.5",
        max_length: int = 512,
        batch_size: int = 16,
    ):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        self.name = model_repo
        self.batch_size = batch_size
        model_path = hf_hub_download(model_repo, "onnx/model.onnx", cache_dir=str(cache_dir))
        tokenizer_path = hf_hub_download(model_repo, "tokenizer.json", cache_dir=str(cache_dir))

        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")  # noqa: S106 - a token name, not a secret
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self.session.get_inputs()}
        self.dim = int(self.session.get_outputs()[0].shape[-1])
        log.info("embedding model loaded", extra={"fields": {"model": model_repo, "dim": self.dim}})

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        out = []
        for start in range(0, len(texts), self.batch_size):
            batch = self.tokenizer.encode_batch(list(texts[start : start + self.batch_size]))
            ids = np.array([e.ids for e in batch], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": np.array([e.attention_mask for e in batch], dtype=np.int64)}
            if "token_type_ids" in self._inputs:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feeds)[0]  # (batch, tokens, dim)
            out.append(hidden[:, 0])  # CLS token
        return _normalise(np.concatenate(out))

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts)

    def embed_query(self, text: str) -> np.ndarray:
        return self._encode([self.QUERY_PREFIX + text])[0]


def default_embedder(cache_dir: Path, model_repo: str) -> Embedder:
    return OnnxBgeEmbedder(cache_dir=cache_dir, model_repo=model_repo)
