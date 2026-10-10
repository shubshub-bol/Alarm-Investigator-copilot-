// Response shapes of the copilot backend (apps/backend/copilot). Only the fields the GUI uses.

export type Severity = "low" | "medium" | "high" | "critical";
export type Priority = "P1" | "P2" | "P3" | "P4";

export interface Citation {
  key: string;
  label: string;
  doc_id: string;
  title: string;
  doc_type: string;
  section_title: string;
  revision: string;
  source_path: string;
  chunk_id: string;
  snippet: string;
}

export interface Statement {
  text: string;
  citations: string[];
}

export interface ActionAssessment {
  step: number;
  action: string;
  verdict: "consistent" | "conflict" | "not_covered";
  document_guidance: string | null;
  citation_keys: string[];
  explanation: string;
}

export interface Fact {
  key: string;
  step: number;
  tool: string;
  statement: string;
}

export interface Grounding {
  mode: "llm" | "template";
  degraded: boolean;
  degraded_reason: string | null;
  removed_statements: number;
  unknown_citations: string[];
  retrieval_confidence: string;
  model: string | null;
  llm_latency_ms: number | null;
}

export interface CopilotAnswer {
  summary: string;
  findings: Statement[];
  likely_causes: Statement[];
  procedure: Statement[];
  recommended_actions: Statement[];
  action_assessments: ActionAssessment[];
  gaps: string[];
  facts: Fact[];
  citations: Citation[];
  grounding: Grounding;
  markdown: string;
  suggestions: string[];
}

export interface ApiCall {
  method: string | null;
  path: string | null;
  status_code: number | null;
  attempts: number;
  duration_ms: number | null;
  outcome: "ok" | "error";
  error_code: string | null;
}

export interface ToolCallRecord {
  kind: "mcp_tool";
  step: number;
  server: string;
  tool: string;
  purpose: string;
  arguments: Record<string, unknown>;
  status: "ok" | "error" | "invalid_input" | "unavailable" | "skipped";
  error_code: string | null;
  error_message: string | null;
  result: Record<string, unknown> | null;
  api_calls: ApiCall[];
  retries: number;
  duration_ms: number;
  trace_id: string;
}

export interface RetrievedRef {
  key: string | null;
  label: string;
  chunk_id: string;
  dense_score: number;
  keyword_score: number;
}

export interface RetrievalRecord {
  kind: "rag_retrieval";
  step: number;
  purpose: string;
  query: string;
  filters: Record<string, unknown>;
  status: "ok" | "error";
  confidence: string | null;
  top_dense_score: number | null;
  results: RetrievedRef[];
  quarantined_withheld: number;
  error_message: string | null;
  duration_ms: number;
  trace_id: string;
}

export type TraceStep = ToolCallRecord | RetrievalRecord;

export interface Discovery {
  kind: "mcp_discovery";
  server: string;
  connected: boolean;
  tool_count: number;
  tools: string[];
  error: string | null;
  duration_ms: number;
}

export type Intent =
  | "investigate_asset"
  | "active_alarms"
  | "highest_priority"
  | "related_assets"
  | "procedure_lookup"
  | "recommendation_check"
  | "knowledge_question"
  | "conversation";

export interface Understanding {
  intent: Intent;
  asset_query: string | null;
  asset_type: string | null;
  site: string | null;
  unit: string | null;
  lookback_days: number | null;
  severities: Severity[] | null;
  alarm_topic: string | null;
  uses_context: boolean;
  method: "rules" | "llm";
  fallback_reason: string | null;
}

export interface AssetSummary {
  asset_id: string;
  name: string;
  asset_type: string;
  site: string;
  unit: string;
  criticality: string;
  manufacturer?: string;
  model?: string;
}

export interface AlarmRecord {
  alarm_id: string;
  asset_id: string;
  asset_name: string;
  alarm_code: string;
  alarm_name: string;
  severity: Severity;
  status: "active" | "cleared";
  start_time: string;
  value: number | null;
  setpoint: number | null;
  uom: string | null;
}

export interface AlarmRef {
  asset_id: string;
  asset_name: string;
  alarm_code: string;
  alarm_name: string;
  occurrences: number;
}

export interface CorrelationPair {
  leading: AlarmRef;
  following: AlarmRef;
  relation: "precedes" | "simultaneous";
  cooccurrences: number;
  confidence: number;
  median_lead_minutes: number;
  insight: string;
}

export interface RankedAlarm {
  alarm_id: string;
  asset_id: string;
  asset_name: string;
  alarm_code: string;
  alarm_name: string;
  severity: Severity;
  score: number;
  priority: Priority;
  priority_label: string;
}

export interface PrioritySnapshot {
  alarm_id?: string;
  score: number;
  priority: Priority;
  priority_label: string;
}

export interface RelatedAssetStatus {
  asset_id: string;
  name: string;
  asset_type: string;
  active_alarms: { alarm_id: string; alarm_name: string; severity: Severity }[];
  alarms_prior_7_days: number;
}

export interface Evidence {
  intent: Intent;
  lookback_days: number | null;
  asset: AssetSummary | null;
  related_assets: AssetSummary[];
  top_alarm: {
    alarm_code: string;
    alarm_name: string;
    alarm_count: number;
    recurring_rate: number | null;
    avg_ack_delay: number | null;
    critical_count?: number;
  } | null;
  trend: {
    direction: "increasing" | "decreasing" | "stable";
    first_half_avg: number;
    second_half_avg: number;
    change_percent: number | null;
  } | null;
  contributing_factors: CorrelationPair[];
  active_alarms: AlarmRecord[];
  ranked_alarms: RankedAlarm[];
  focus_alarm: AlarmRecord | null;
  priority: PrioritySnapshot | null;
  recommendations: {
    likely_causes: string[];
    recommended_actions: { step: number; action: string }[];
    source: string;
    related_assets: RelatedAssetStatus[] | null;
  } | null;
  passages: { chunk_id: string; text: string; citation: Citation }[];
  passage_purposes: Record<string, string>;
  retrieval_confidence: string;
  notes: string[];
  missing: string[];
}

export interface ChatResponse {
  conversation_id: string;
  trace_id: string;
  question: string;
  status: "ok" | "partial" | "degraded";
  understanding: Understanding;
  answer: CopilotAnswer;
  evidence: Evidence;
  discovery: Discovery | null; // null when no tools were needed (small talk)
  trace: TraceStep[];
  timings: { understanding_ms: number; investigation_ms: number; answer_ms: number; total_ms: number };
}

export interface Health {
  status: "ok" | "degraded";
  version: string;
  mcp: { server: string; connected: boolean; tools: number; error: string | null };
  rag: { index_loaded: boolean; chunks: number; embedder: string | null };
  llm: { provider: string };
  storage?: { ok: boolean; conversations?: number; error?: string }; // absent from backends older than saved chats
}

export interface ToolSpec {
  server: string;
  name: string;
  title: string | null;
  description: string;
  input_schema: JsonSchema;
  output_schema: JsonSchema | null;
  read_only: boolean;
}

export interface ToolsResponse {
  server: string;
  connected: boolean;
  error: string | null;
  tools: ToolSpec[];
}

export interface JsonSchema {
  type?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  anyOf?: JsonSchema[];
  items?: JsonSchema;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  pattern?: string;
  default?: unknown;
  format?: string;
  $ref?: string;
}

// Saved chats (GET/PATCH/DELETE /api/conversations)

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StoredTurn {
  question: string;
  created_at: string;
  response: ChatResponse;
}

export interface ConversationDetail extends ConversationSummary {
  turns: StoredTurn[];
}
