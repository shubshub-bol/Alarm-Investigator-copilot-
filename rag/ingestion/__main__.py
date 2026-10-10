"""Build or check the retrieval index.

python -m rag.ingestion            # (re)build rag/index from rag/documents with the bge model
python -m rag.ingestion --check    # exit 1 if documents changed since the index was built
python -m rag.ingestion --embedder hashing   # no model download (tests / constrained machines)
"""

import argparse
import sys

from rag.config import RagSettings
from rag.embeddings import HashingEmbedder, default_embedder
from rag.ingestion.pipeline import check_index, ingest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report whether the index is up to date")
    parser.add_argument("--embedder", choices=["onnx", "hashing"], default="onnx")
    args = parser.parse_args()
    settings = RagSettings()

    if args.check:
        status = check_index(settings)
        print(f"Index: {status.reason}")
        for label, ids in (("changed", status.changed), ("added", status.added), ("removed", status.removed)):
            if ids:
                print(f"  {label}: {', '.join(ids)}")
        return 0 if status.up_to_date else 1

    if args.embedder == "hashing":
        embedder = HashingEmbedder()
    else:
        embedder = default_embedder(settings.model_cache_dir, settings.embedding_model)
    report = ingest(settings, embedder)

    print(f"{'doc_id':<16} {'type':<22} {'sections':>8} {'chunks':>7} {'flagged':>8}")
    for d in report.per_document:
        print(f"{d['doc_id']:<16} {d['doc_type']:<22} {d['sections']:>8} {d['chunks']:>7} {d['flagged_chunks']:>8}")
    print(
        f"\n{report.documents} documents -> {report.chunks} chunks ({report.flagged_chunks} quarantined), "
        f"embedder={report.embedder}, {report.duration_s}s -> {report.index_dir}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
