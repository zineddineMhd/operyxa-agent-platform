// Typed client for the Operyxa REST API. The token lives in localStorage for the MVP.

export type RunStatus = "pending" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";
export type StepStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export interface User { id: string; email: string; role: string; created_at: string }
export interface Project { id: string; name: string; repo_owner: string; repo_name: string; created_at: string; has_token: boolean }
export interface Limits { max_llm_calls: number; max_tool_calls: number; max_seconds: number; max_cost_usd: number }
export interface PlanStep { id: string; agent: string; action: string; depends_on: string[] }
export interface Run {
  id: string; project_id: string; objective: string; workflow_type: string; status: RunStatus;
  stop_reason: string | null; error: { code: string; message: string } | null;
  commit_sha: string | null; plan: { steps: PlanStep[] } | null; limits: Limits;
  created_at: string; started_at: string | null; completed_at: string | null;
}
export interface Usage { llm_calls: number; tool_calls: number; cost_usd: number; input_tokens: number; output_tokens: number }
export interface RunDetail extends Run { usage: Usage }
export interface Step {
  step_key: string; position: number; agent: string; action: string; depends_on: string[];
  status: StepStatus; summary: string | null; error: string | null;
  started_at: string | null; completed_at: string | null;
}
export interface ToolCall {
  id: string; evidence_id: string; step_key: string; agent: string; tool: string;
  args: Record<string, unknown>; risk: string; decision: string;
  status: "completed" | "failed" | "rejected"; error_code: string | null; truncated: boolean;
  attempts: number; duration_ms: number; created_at: string;
}
export interface ToolCallDetail extends ToolCall { result_text: string | null }
export interface EvidenceRef {
  evidence_id: string; quote: string; verified: boolean; tool: string | null;
  path: string | null; start_line: number | null; end_line: number | null; reason?: string;
}
export interface Finding {
  id: string; step_key?: string; step?: string; agent: string; title: string; severity: string;
  category: string; description: string; recommendation: string; status: string; evidence: EvidenceRef[];
}
export interface Report {
  run_id: string; partial: boolean; summary: string; markdown: string; created_at: string;
  body: {
    objective: string; repository: string; commit_sha: string; summary: string;
    recommendations: string[]; stop_reason: string | null; findings: Finding[];
    unverified_findings: Finding[]; usage: Usage; disclaimer: string;
    steps: { id: string; agent: string; action: string; status: string; summary: string | null; error: string | null }[];
  };
}
export interface Approval {
  id: string; run_id: string; step_key: string; agent: string; tool: string; args: Record<string, unknown>;
  args_hash: string; risk: string; status: "pending" | "approved" | "rejected" | "expired"; reason: string | null;
  requested_at: string; expires_at: string; decided_at: string | null;
}
export interface RunEvent { id: number; type: string; data: Record<string, unknown>; created_at: string }
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export interface Overview {
  runs_total: number; runs_this_week: number; runs_by_status: Partial<Record<RunStatus, number>>;
  success_rate: number | null; pending_approvals: number; estimated_cost_usd: number;
  avg_run_cost_usd: number | null; llm_calls: number; llm_provider: string;
}
export interface PlatformConfig {
  llm_provider: string; llm_model: string; llm_fallback_model: string | null;
  private_repositories: boolean; read_only: boolean; limits: Limits;
}
export interface CatalogTool { server: string; tool: string; risk: string; policy: string; agents: string[]; access: string }

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) {
    super(message);
  }
}

const TOKEN_KEY = "operyxa.token";
export const BASE = "/api/v1";
let onUnauthorized: () => void = () => {};

export const auth = {
  get token(): string | null {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  set(token: string | null) {
    try { token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ }
  },
  onUnauthorized(handler: () => void) { onUnauthorized = handler; },
};

export function authHeaders(): Record<string, string> {
  return auth.token ? { Authorization: `Bearer ${auth.token}` } : {};
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders(), ...(init.headers as Record<string, string>) };
  if (init.body) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(BASE + path, { ...init, headers });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "The API is unreachable. Check that the backend is running.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = body?.error;
    if (response.status === 401 && error?.code !== "INVALID_CREDENTIALS") onUnauthorized();
    throw new ApiError(response.status, error?.code ?? "HTTP_ERROR",
      error?.message ?? `The server answered ${response.status}.`, error?.request_id);
  }
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? response.json() : response.text()) as Promise<T>;
}

const post = <T,>(path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body), headers });

export const api = {
  register: (email: string, password: string) => post<User>("/auth/register", { email, password }),
  login: (email: string, password: string) =>
    post<{ access_token: string }>("/auth/login", { email, password }),
  me: () => request<User>("/users/me"),
  projects: () => request<Page<Project>>("/projects?limit=100"),
  createProject: (name: string, repository_url: string, github_token?: string) =>
    post<Project>("/projects", { name, repository_url, ...(github_token ? { github_token } : {}) }),
  runs: (params: { limit?: number; offset?: number; status?: string } = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") query.set(key, String(value));
    return request<Page<Run>>(`/runs?${query}`);
  },
  startRun: (project_id: string, objective: string) =>
    post<{ run_id: string; status: RunStatus }>("/runs", { project_id, objective },
      { "Idempotency-Key": crypto.randomUUID() }),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  steps: (id: string) => request<Step[]>(`/runs/${id}/steps`),
  cancelRun: (id: string) => post<Run>(`/runs/${id}/cancel`),
  toolCalls: (id: string) => request<Page<ToolCall>>(`/runs/${id}/tool-calls?limit=100`),
  evidence: (id: string, evidenceId: string) => request<ToolCallDetail>(`/runs/${id}/evidence/${evidenceId}`),
  eventHistory: (id: string) => request<RunEvent[]>(`/runs/${id}/events/history?limit=500`),
  report: (id: string) => request<Report>(`/runs/${id}/report`),
  approvals: (status: string) => request<Approval[]>(`/approvals?status=${status}`),
  decideApproval: (id: string, decision: "approve" | "reject") =>
    post<Approval>(`/approvals/${id}/decision`, { decision }),
  overview: () => request<Overview>("/analytics/overview"),
  config: () => request<PlatformConfig>("/platform/config"),
  tools: () => request<{ tools: CatalogTool[] }>("/mcp/tools"),
};
