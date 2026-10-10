import type { ReactElement } from "react";
import type { Priority } from "../api/types";

// Redundant coding as on ISA-101 displays: colour + shape + text, so priority never depends on colour alone.
const SHAPES: Record<Priority, ReactElement> = {
  P1: <rect x="1" y="1" width="8" height="8" />,
  P2: <polygon points="5,0.5 9.5,9 0.5,9" />,
  P3: <polygon points="5,0 10,5 5,10 0,5" />,
  P4: <circle cx="5" cy="5" r="4.2" />,
};

interface Props {
  priority: Priority;
  score?: number;
  label?: string;
}

export function PriorityBadge({ priority, score, label }: Props) {
  const text = score == null ? priority : `${priority} ${Math.round(score)}`;
  return (
    <span className="prio" data-p={priority} title={label ? `${priority}: ${label}` : priority}>
      <svg viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" fill="currentColor">
        {SHAPES[priority]}
      </svg>
      <span>{text}</span>
    </span>
  );
}
