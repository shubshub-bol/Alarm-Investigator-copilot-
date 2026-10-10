interface Props {
  value: unknown;
  label: string;
}

/** Raw request or response, shown as formatted text (never interpreted as HTML). */
export function JsonBlock({ value, label }: Props) {
  return (
    <pre className="json" aria-label={label} tabIndex={0}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
