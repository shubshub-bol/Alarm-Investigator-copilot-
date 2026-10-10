// Citation keys: [T#] = a tool fact from a trace step, [S#] = a document passage.

export type Segment = { kind: "text"; text: string } | { kind: "cite"; key: string };

const KEY = /\[([TS]\d+)\]/g;

/** Split running text such as "Rising [T2] per SOP [S1]." into text and citation segments. */
export function splitCitations(text: string): Segment[] {
  const segments: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(KEY)) {
    const index = match.index ?? 0;
    if (index > last) segments.push({ kind: "text", text: text.slice(last, index) });
    segments.push({ kind: "cite", key: match[1] });
    last = index + match[0].length;
  }
  if (last < text.length) segments.push({ kind: "text", text: text.slice(last) });
  return segments;
}

export const isToolKey = (key: string) => key.startsWith("T");
