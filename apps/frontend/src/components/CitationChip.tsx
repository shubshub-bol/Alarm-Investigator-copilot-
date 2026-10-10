import { isToolKey } from "../lib/citations";

interface Props {
  citeKey: string;
  selected: boolean;
  onSelect: (key: string) => void;
  title?: string;
}

/** [T#] is filled (measured data from a tool), [S#] is outlined (a document). Click shows the evidence. */
export function CitationChip({ citeKey, selected, onSelect, title }: Props) {
  const tool = isToolKey(citeKey);
  const label = `${tool ? "Tool fact" : "Document source"} ${citeKey.slice(1)}`;
  return (
    <button
      type="button"
      className={`chip ${tool ? "chip-tool" : "chip-doc"}`}
      aria-pressed={selected}
      aria-label={`${label}: show evidence`}
      title={title ?? label}
      onClick={() => onSelect(citeKey)}
    >
      {citeKey}
    </button>
  );
}
