import type { Turn } from "../../state/useChat";
import { CitationChip } from "../CitationChip";

const DOC_TYPE: Record<string, string> = {
  operating_procedure: "Operating procedure",
  maintenance_manual: "Maintenance manual",
  troubleshooting_guide: "Troubleshooting guide",
  safety_instruction: "Safety instruction",
  alarm_philosophy: "Alarm philosophy",
  knowledge_article: "Knowledge article",
  vendor_bulletin: "Vendor bulletin",
};

/** Document passages cited in the answer, with where they came from and why they were retrieved. */
export function SourcesPanel({
  turn,
  highlightKey,
  onCite,
}: {
  turn: Turn;
  highlightKey: string | null;
  onCite: (key: string) => void;
}) {
  const response = turn.response;
  if (!response) return <p className="muted">Sources appear here when the investigation finishes.</p>;
  const { citations } = response.answer;
  if (citations.length === 0) {
    return <p className="muted">No document passages were relevant enough to cite for this question.</p>;
  }
  const textByKey = new Map(response.evidence.passages.map((p) => [p.citation.key, p.text]));
  return (
    <ol className="sources">
      {citations.map((c) => (
        <li
          key={c.key}
          id={`${turn.id}-source-${c.key}`}
          tabIndex={-1}
          className="source"
          data-target={highlightKey === c.key}
        >
          <header>
            <CitationChip citeKey={c.key} selected={highlightKey === c.key} onSelect={onCite} />
            <span className="source-label">{c.label}</span>
            <span className="muted">{DOC_TYPE[c.doc_type] ?? c.doc_type}</span>
          </header>
          <p className="source-title">
            {c.title}, revision {c.revision}
          </p>
          {response.evidence.passage_purposes[c.key] && (
            <p className="muted">Retrieved for: {response.evidence.passage_purposes[c.key]}</p>
          )}
          <blockquote className="passage">{textByKey.get(c.key) ?? c.snippet}</blockquote>
          <p className="muted source-path">{c.source_path}</p>
        </li>
      ))}
    </ol>
  );
}
