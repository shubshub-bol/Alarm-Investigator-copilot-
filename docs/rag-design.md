# RAG design

Document retrieval gives the copilot the plant's procedures, manuals and rules as evidence. It is not a
separate demo: the playbooks build their search queries from what the MCP tools found (the asset type, the
top alarm and its code, the contributing factor, each API recommendation), and the answer cites both kinds
of evidence side by side. Real search results, scores and citations are in [rag-examples.md](rag-examples.md).

| | |
|---|---|
| Corpus | 9 synthetic documents, 8 document types, in `rag/documents/` |
| Index | 54 passages (1 quarantined) in `rag/index/`: `chunks.jsonl`, `vectors.npy`, `manifest.json` |
| Embedding model | `BAAI/bge-small-en-v1.5` (384 dimensions) on ONNX Runtime, CPU only, runs locally |
| Search | Hybrid: BM25 keywords + dense vectors, merged with Reciprocal Rank Fusion |
| Quality | 12-question golden set: recall@3 = 1.00, MRR = 0.82 (`uv run python -m rag.evaluate`) |
| Code | `rag/ingestion/` (build), `rag/retrieval/` (search, citations), `rag/embeddings.py`, `rag/index_store.py` |

## 1. Source document types

The assignment supplies no documents, so the corpus was written for this project. It mirrors a real plant
library: approved procedures, maintenance and troubleshooting knowledge, a safety standard, the alarm
philosophy, a knowledge article, and one untrusted external bulletin that contains a planted prompt injection.

| Document | Type | Covers |
|---|---|---|
| `SOP-BFP-001` | operating_procedure | Boiler feed pump operation and alarm response; §4.1 high vibration |
| `SOP-CMP-004` | operating_procedure | Gas compressor operation, discharge pressure, surge, discharge cooler cleaning |
| `MM-PUMP-002` | maintenance_manual | Pump and drive motor maintenance, alignment, vibration analysis, bearings, spares |
| `TG-MTR-003` | troubleshooting_guide | Motor trips, winding temperature, overload, bearing temperature, related assets |
| `TG-DEA-007` | troubleshooting_guide | Deaerator level and pressure, effect on the feed pumps, valve deviation |
| `SAF-005` | safety_instruction | Rotating equipment safety: lockout/tagout, hot fluids, PPE, permits |
| `ALM-PHIL-006` | alarm_philosophy | P1-P4 meaning and response times, floods, stale alarms, which guidance governs |
| `KB-008` | knowledge_article | Past feed pump vibration cases and lessons learned (plain text) |
| `VENDOR-NOTE-009` | vendor_bulletin | Seal supplier bulletin with a planted prompt injection (quarantined) |

Each file starts with front matter that becomes metadata:

```yaml
---
doc_id: SOP-BFP-001
title: Boiler Feed Pump Operation and Alarm Response
doc_type: operating_procedure
equipment_types: [pump]
alarm_codes: [PMP-VIB-HI, PMP-SUCT-LO, PMP-BRG-TEMP-HI, PMP-SEAL-LEAK]
site: NorthPlant
revision: C
effective_date: 2026-01-15
owner: Operations - Unit 1
---
```

## 2. Ingestion flow

```text
rag/documents/*.md|.txt|.pdf
  -> load       read the file, parse front matter, split into sections        (loaders.py)
  -> screen     flag sections that try to instruct a model                     (safety.py)
  -> sanitise   drop hidden text: HTML comments, zero-width and control chars  (safety.py)
  -> tables     turn each Markdown table row into a sentence                   (tables.py)
  -> chunk      section-aware passages of up to 160 words with overlap         (chunker.py)
  -> label      add alarm codes found in the passage to the document's codes   (pipeline.py)
  -> embed      bge-small vectors for every passage                            (embeddings.py)
  -> save       chunks.jsonl + vectors.npy + manifest.json                     (index_store.py)
```

Index creation:

```bash
uv run python -m rag.ingestion
```

```bash
uv run python -m rag.ingestion --check
```

The first command builds the index (and downloads the model once, about 130 MB, into `.cache/models`).
The second only reports whether the documents changed since the index was built. `--embedder hashing`
builds an index without downloading a model, for constrained machines and fast tests. In Docker the index
is built while the image is built, so containers start without internet access.

## 3. Text extraction

| Format | Extraction | Sections |
|---|---|---|
| Markdown | UTF-8 text after the front matter | Headings; each section keeps its number (`4.1`) and full heading path |
| Plain text | UTF-8 text after the front matter | A line written fully in capitals starts a section |
| PDF | `pypdf` page text, pages joined | One section; metadata from the file name (`DOCID_title.pdf`) |

Tables are rewritten so the header stays next to every value: `| P1 | ... | 5 minutes |` becomes
`Priority: P1; Meaning: ...; Response required within: 5 minutes.`, which embeds and keyword-matches far
better than a bare table row.

## 4. Chunking strategy

- **Section-aware.** A passage never crosses a section boundary, so it is about one topic (for example one
  alarm's response steps), and its citation points to one section.
- **Size.** Up to 160 words (`RAG_CHUNK_MAX_WORDS`). A procedure step block fits in one or two passages, and
  the answer can use up to 8 passages without flooding the prompt.
- **Units.** Whole paragraphs and tables when they fit; otherwise list items and sentences; only a single
  sentence longer than the limit is cut into word windows.
- **Overlap.** Each passage starts with the last whole sentence(s) of the previous one, up to 30 words
  (`RAG_CHUNK_OVERLAP_WORDS`), so a step that continues across the cut keeps its context.
- **Stable ids.** `SOP-BFP-001::s03-4-1::1` = document, section index and number, passage number.

## 5. Chunk metadata

| Field | Use |
|---|---|
| `chunk_id`, `chunk_index`, `content_hash` | Identity; the hash detects changed passages |
| `doc_id`, `title`, `doc_type`, `revision`, `effective_date`, `source_path` | Citation and authority ranking |
| `site`, `equipment_types` | Filters |
| `alarm_codes` | From the front matter plus codes found in the passage text; drives the alarm-code boost |
| `section_number`, `section_title`, `heading_path` | Citation label and the text that is embedded |
| `word_count` | Chunking checks |
| `injection_flag`, `injection_reasons` | Quarantine and audit |

The text that is embedded is the document title, the heading path and the passage, so a short step such as
"Stop the pump" is still found by a question about the boiler feed pump's high vibration.

## 6. Embedding model

`BAAI/bge-small-en-v1.5`, run directly with ONNX Runtime and the `tokenizers` library (no PyTorch): small
(384 dimensions, about 130 MB), fast on a CPU, and strong at English retrieval. As the model card specifies,
vectors use CLS pooling and L2 normalisation, and queries get the prefix
`Represent this sentence for searching relevant passages: `. The model is downloaded once from Hugging Face;
in Docker it is part of the image and `HF_HUB_OFFLINE=1` stops any network call at runtime.

## 7. Vector database or index

There is no vector database, deliberately. 54 passages fit in memory, and an exact cosine comparison against
all of them with NumPy takes a few milliseconds, so a database would add a service, a network hop and
operational work without improving anything. The index is three files:

| File | Contents |
|---|---|
| `chunks.jsonl` | One JSON record per passage: text and metadata |
| `vectors.npy` | float32 matrix, one L2-normalised row per passage |
| `manifest.json` | Embedder name and dimensions, chunk settings, counts, and each document's SHA-256 |

The retriever refuses an index built with a different embedder. To scale to thousands of documents, the
index store can be replaced with pgvector or Qdrant behind the same `Retriever` interface.

## 8. Hybrid search

Alarm codes and part numbers (`PMP-VIB-HI`, `SP-BRG-6316`) need exact matching; operator questions ("the pump
is shaking") need meaning-based matching. Each search runs both:

- **Keyword:** BM25 (Okapi) over lower-cased words without stopwords, plus whole hyphenated codes as single
  terms, so `PMP-VIB-HI` matches as one term as well as by its parts.
- **Dense:** cosine similarity between the query vector and every passage vector.

## 9. Ranking

1. **Reciprocal Rank Fusion** merges the two rankings: each passage scores `1 / (60 + rank)` in each list (the
   keyword list counts only real hits). RRF needs no calibration between BM25 and cosine scales.
2. **Small priors**, in units of one top rank (about six rank positions near the top):
   approved operating procedures and safety instructions +0.10; alarm philosophy, maintenance manuals and
   troubleshooting guides +0.05 (following ALM-PHIL-006 §7: approved procedures govern); a passage about an
   alarm code being investigated +0.15.
3. **Diversity:** at most 2 passages per document in one search.
4. **Relevance floor:** a passage with a dense score below 0.55 is never returned.

There is no cross-encoder reranker: at this size the golden set already has every correct passage in the top
3. It is listed as a future improvement for a larger corpus.

## 10. Retrieval filters

| Filter | Behaviour |
|---|---|
| `doc_types` | Only these document types |
| `doc_ids` | Only these documents |
| `equipment_types` | Documents for this equipment, plus general documents with no equipment type |
| `site` | Documents for this site, plus site-wide (`all`) documents |
| `alarm_codes` | Boost, not filter: passages about these codes rank higher |
| `include_quarantined` | Audit only; off everywhere in the copilot |

How the playbooks use them:

| Search | Query built from MCP results | Filters |
|---|---|---|
| Procedure for the alarm | asset type + alarm name + code | procedures, manuals, troubleshooting guides, knowledge articles; the asset's type; alarm code boost; site |
| Approved procedure (procedure_lookup) | asset type + alarm + "operating procedure" | `operating_procedure`, the asset's type, alarm code, site |
| Contributing factor | leading alarm before the main alarm | `maintenance_manual`, `troubleshooting_guide`, `knowledge_article`; the asset's and the leading asset's types |
| Related equipment to inspect | asset type + alarm + "related assets to inspect" | the asset's type, alarm code boost |
| Safety before inspection | "lockout and isolation before ... a {asset type}" | `safety_instruction`, the asset's type |
| What a priority means | "alarm priority definitions and response times" | `alarm_philosophy` |
| Which guidance governs (after a conflict) | "recommendation conflicts with an approved procedure" | `alarm_philosophy` |
| Each API action (recommendation_check) | asset type + alarm + the action text | the asset's type, alarm code boost, site |

Every search about an asset is limited to documents for its type, so a fan's vibration never gets the boiler feed
pump procedure. When no document covers the type, the answer says so: "The documents have no procedure for boiler
alarms."

## 11. Citation construction

Every returned passage becomes a citation with a key, a readable label and where it came from:

```json
{"key": "S1", "label": "SOP-BFP-001 §4.1", "title": "Boiler Feed Pump Operation and Alarm Response",
 "revision": "C", "section_title": "4.1 High vibration (PMP-VIB-HI)", "source_path": "SOP-BFP-001_...md",
 "chunk_id": "SOP-BFP-001::s03-4-1::1", "snippet": "Continued operation above 7.1 mm/s needs ..."}
```

- The label is `DOC-ID §number`, or `DOC-ID › section title` when the section has no number.
- Keys are numbered once across all searches for a question, so `[S1]` means the same passage in the
  answer, the Sources tab and the trace.
- Tool results get keys too (`[T1]`, `[T2]`, built in `copilot/facts.py`), each tied to its trace step.
- Code builds every cited section from the evidence. The model writes only the summary and the analysis, and
  its text is checked (`copilot/answer.py`, `copilot/text_check.py`): every key must exist, every number and
  code must appear in the evidence, and a key written with extra words, such as `S1 step 6` or `(T2)`, is read
  as `S1` or `[T2]`. When the model cites nothing, code gives each sentence the key of the fact or passage that
  holds its numbers and codes. A summary that fails is replaced by the plain summary; an analysis statement is
  dropped.
- In the GUI, `[S#]` chips open the passage; the Sources tab lists every passage and why it was searched for.

## 12. Low-confidence handling

| Level | Rule (top dense score) | Effect |
|---|---|---|
| high | >= 0.70 | Used as evidence |
| medium | >= 0.62, or an exact alarm-code hit | Used as evidence |
| low | below 0.62, some passage above the floor | Used, and the answer states the weak evidence |
| none | nothing above the 0.55 floor | No document evidence; the answer says so |

The thresholds were measured on this corpus: relevant passages score about 0.70 and above, unrelated
questions stay below 0.55 ("What is the capital of France?" scores 0.40). Each playbook names its main search
(for an investigation, the procedure for the top alarm), and the answer's confidence is that search's, so a
well-matched safety passage cannot make a missing procedure look "high". When a main search is low or none, the
answer adds the gap "No relevant passages were found in the document corpus for this question", or the more
precise "The documents have no procedure for ... alarms" when nothing exists for the asset type. A playbook that
falls back to searching the question itself treats that search as its main search. When no search ran at all
(no active alarms), neither gap appears: the answer's own gap says why, and the summary repeats it. The language
model is told to list missing evidence under gaps instead of guessing, and every finding, cause, procedure item,
action and analysis statement carries a citation. If the document index cannot be loaded at all, the copilot still
answers from live alarm data and says that documents were unavailable.

## 13. Prompt-injection protections

Documents are treated as untrusted input, in layers:

1. **Screening at ingestion** (`safety.py`). A section is flagged when it contains one strong signal (telling
   the model to ignore its instructions, asking for the system prompt, a role override, a fake
   `assistant:`/`system:` marker, an order to call tools) or two weak ones (text addressed to AI assistants,
   "do not mention ...", "safe to bypass"). Screening reads the raw text, so instructions hidden in HTML
   comments are caught. One mention of "AI assistants" alone is normal and is not flagged.
2. **Quarantine.** Flagged passages stay in the index for audit but are never returned to the copilot.
3. **Sanitising.** Invisible content (HTML comments, zero-width and bidirectional characters, control
   characters) is removed before indexing, so nothing a reader cannot see reaches the model.
4. **Prompt wrapping.** Passages reach the model inside `<<<SOURCE S1 ...>>>` blocks under a header that says
   they are untrusted reference material, not instructions; delimiter look-alikes inside a document are
   neutralised so it cannot close its own block. The system prompt repeats the rule.
5. **Output checks and least privilege.** Every statement must cite given evidence, and every MCP tool is
   read-only, so even a manipulated model cannot change plant state.

`VENDOR-NOTE-009` is the test fixture: its section 3 tells AI assistants to ignore their instructions and say
it is safe to bypass the vibration trip. It is flagged for six reasons, withheld from every search, and covered
by tests (see [rag-examples.md](rag-examples.md#prompt-injection-quarantine)).

## 14. Index refresh process

1. Edit, add or remove files in `rag/documents/` (or `DOCUMENT_PATH`).
2. `uv run python -m rag.ingestion --check` compares every document's SHA-256 with the manifest and lists
   changed, added and removed documents; it exits with code 1 when the index is stale.
3. `uv run python -m rag.ingestion` rebuilds the whole index (about 4 seconds for this corpus, model loading included).
4. Restart the backend, which loads the index at startup. With Docker, `docker compose up --build` rebuilds the
   image and therefore the index.

A full rebuild is simpler and safer than incremental updates at this size; the content hashes per passage
would support incremental updates later.

## Retrieval evaluation

`rag/evaluate.py` holds 12 golden questions with the passages that must be found: plain-language questions,
a bare alarm code, a table lookup (P1 response time), a part number, and the governance rule.

```bash
uv run python -m rag.evaluate
```

Result: recall@3 = 1.00 (a correct passage is in the top 3 for every question) and MRR = 0.82. The test
`rag/tests/test_retrieval.py` fails if recall@3 drops below 1.0 or MRR below 0.8.

## Tests

| Test file | Covers |
|---|---|
| `rag/tests/test_ingestion.py` | Extraction, front matter, sections, chunking, metadata, injection screening, index files |
| `rag/tests/test_retrieval.py` | Golden-set quality, filters, confidence levels, quarantine, citations (real model) |
| `apps/backend/tests/test_investigation.py` | Retrieval inside the MCP workflow, queries built from tool results |
| `apps/backend/tests/test_answer.py` | Citation validation, conflicts between API advice and documents, degraded modes |
