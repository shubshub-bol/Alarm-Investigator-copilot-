import type { JsonSchema, ToolsResponse } from "../../api/types";

/** MCP tool discovery view: what the copilot can call, with each tool's input contract. */
export function ToolsPanel({
  tools,
  error,
  onReload,
}: {
  tools: ToolsResponse | null;
  error: string | null;
  onReload: () => void;
}) {
  if (error || (tools && !tools.connected)) {
    return (
      <div className="failure" role="alert">
        <p>Tool discovery failed. {error ?? tools?.error}</p>
        <button type="button" className="button" onClick={onReload}>
          Discover again
        </button>
      </div>
    );
  }
  if (!tools) return <p className="muted">Discovering tools…</p>;
  return (
    <div className="tools">
      <p className="muted">
        {tools.tools.length} tools discovered on <code className="tag">{tools.server}</code>. All are read-only.
      </p>
      <ul>
        {tools.tools.map((tool) => (
          <li key={tool.name}>
            <details className="tool">
              <summary>
                <code className="tool-name">{tool.name}</code>
                <span className="muted">{tool.title}</span>
              </summary>
              <p>{tool.description}</p>
              <Inputs schema={tool.input_schema} />
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}

function typeOf(schema: JsonSchema): string {
  if (schema.enum) return schema.enum.map(String).join(" | ");
  if (schema.anyOf) return schema.anyOf.map(typeOf).filter((t) => t !== "null").join(" | ");
  if (schema.type === "array" && schema.items) return `list of ${typeOf(schema.items)}`;
  return schema.format ?? schema.type ?? "object";
}

function limits(schema: JsonSchema): string {
  const inner = schema.anyOf?.find((s) => s.type !== "null") ?? schema;
  const parts = [];
  if (inner.minimum != null) parts.push(`min ${inner.minimum}`);
  if (inner.maximum != null) parts.push(`max ${inner.maximum}`);
  if (inner.maxLength != null) parts.push(`up to ${inner.maxLength} characters`);
  if (inner.maxItems != null) parts.push(`up to ${inner.maxItems} items`);
  if (schema.default !== undefined && schema.default !== null) parts.push(`default ${JSON.stringify(schema.default)}`);
  return parts.join(", ");
}

function Inputs({ schema }: { schema: JsonSchema }) {
  const properties = Object.entries(schema.properties ?? {});
  if (properties.length === 0) return <p className="muted">No inputs.</p>;
  const required = new Set(schema.required ?? []);
  return (
    <table className="schema">
      <caption className="visually-hidden">Inputs</caption>
      <thead>
        <tr>
          <th scope="col">Input</th>
          <th scope="col">Type</th>
          <th scope="col">Rules</th>
        </tr>
      </thead>
      <tbody>
        {properties.map(([name, prop]) => (
          <tr key={name}>
            <td>
              <code className="tag">{name}</code>
              {required.has(name) && <span className="required"> required</span>}
            </td>
            <td>{typeOf(prop)}</td>
            <td className="muted">{limits(prop) || prop.description}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
