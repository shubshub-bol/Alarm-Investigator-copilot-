"""Retrieval evaluation on a golden question set: recall@k and mean reciprocal rank (MRR).

    python -m rag.evaluate          # uses the built index in rag/index

The model receives the top few passages, so the main metric is whether a correct passage is in the
top k (recall@k). MRR shows how high it ranks on average (1.0 = always first).
"""

import sys
from dataclasses import dataclass

from rag.retrieval import Retriever

GOLDEN: list[tuple[str, set[str]]] = [
    ("Boiler feed pump high vibration, what should the operator do?", {"SOP-BFP-001 §4.1"}),
    ("the pump is shaking a lot", {"SOP-BFP-001 §4.1", "SOP-BFP-001 §4.2", "KB-008 › LESSONS LEARNED"}),
    ("what causes a motor to trip and which related equipment should be inspected", {"TG-MTR-003 §6", "TG-MTR-003 §2"}),
    ("lockout before opening a bearing housing", {"SAF-005 §2"}),
    ("compressor discharge pressure alarm keeps coming back", {"SOP-CMP-004 §3.1"}),
    ("motor bearing gets hot before the pump vibrates", {"MM-PUMP-002 §4", "TG-MTR-003 §5"}),
    ("PMP-SUCT-LO", {"SOP-BFP-001 §4.2"}),
    ("how quickly must a P1 alarm be responded to", {"ALM-PHIL-006 §2"}),
    ("which bearing part number for the BFP-101 pump", {"MM-PUMP-002 §7"}),
    ("deaerator level low and feed pump suction pressure dropping", {"TG-DEA-007 §2", "TG-DEA-007 §1"}),
    ("what to do during an alarm flood", {"ALM-PHIL-006 §4"}),
    ("AI recommendation disagrees with the operating procedure, which one wins", {"ALM-PHIL-006 §7"}),
]


@dataclass
class EvalRow:
    question: str
    expected: set[str]
    retrieved: list[str]
    rank: int | None  # 1-based rank of the first correct passage


@dataclass
class EvalReport:
    k: int
    rows: list[EvalRow]

    @property
    def recall_at_k(self) -> float:
        return sum(r.rank is not None for r in self.rows) / len(self.rows)

    @property
    def mrr(self) -> float:
        return sum(1 / r.rank for r in self.rows if r.rank) / len(self.rows)


def evaluate(retriever: Retriever, k: int = 3, golden: list[tuple[str, set[str]]] = GOLDEN) -> EvalReport:
    rows = []
    for question, expected in golden:
        retrieved = [p.citation.label for p in retriever.search(question, k=k).passages]
        rank = next((i for i, label in enumerate(retrieved, 1) if label in expected), None)
        rows.append(EvalRow(question, expected, retrieved, rank))
    return EvalReport(k, rows)


def main() -> int:
    from rag.config import RagSettings

    report = evaluate(Retriever.from_settings(RagSettings()))
    for row in report.rows:
        mark = f"rank {row.rank}" if row.rank else "MISS  "
        print(f"{mark:<7} {row.question[:70]:<70} -> {', '.join(row.retrieved)}")
    print(f"\nrecall@{report.k} = {report.recall_at_k:.2f}   MRR = {report.mrr:.2f}   ({len(report.rows)} questions)")
    return 0 if report.recall_at_k == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
