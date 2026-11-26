// Typed client for the Operyxa REST API. The token lives in localStorage for the MVP.

export type RunStatus = "pending" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";
export type StepStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export interface User { id: string; email: string; role: string; created_at: string }
export interface Project {
  id: string; name: string; repo_owner: string; repo_name: string; created_at: string; has_token: boolean;
  has_write_token: boolean; team_id: string | null; role?: string | null;
}
export interface Limits { max_llm_calls: number; max_tool_calls: number; max_seconds: number; max_cost_usd: number }
export interface PlanStep { id: string; agent: string; action: string; depends_on: string[] }
export interface Run {
  id: string; project_id: string; objective: string; workflow_type: string; workflow_id?: string | null;
  options?: { run_tests?: boolean; propose_fixes?: boolean; agents?: string[] }; policy?: Record<string, string>;
  status: RunStatus;
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
    unverified_findings: Finding[]; duplicates_merged?: number; usage: Usage; disclaimer: string;
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
  avg_duration_seconds: number | null; verification_rate: number | null; tool_error_rate: number | null;
  verified_by_severity: Record<string, number>; runs_by_workflow: Record<string, number>;
  stop_reasons: Record<string, number>; duplicates_merged: number; tool_rejections: number;
}
export interface ToolMetric { tool: string; calls: number; failed: number; rejected: number; avg_ms: number }
export interface RunMetrics {
  duration_seconds: number | null; verification_rate: number | null; tool_error_rate: number | null;
  verified_by_severity: Record<string, number>; findings_by_status: Record<string, number>; tools: ToolMetric[];
  budget_used: Record<string, number | null>; approvals_by_status: Record<string, number>;
  models?: Record<string, number>;
}
export interface WorkflowTemplate {
  key?: string; id?: string; name: string; description: string; agents: string[];
  objective_template: string; options: { run_tests?: boolean; propose_fixes?: boolean }; builtin?: boolean;
  base_type?: string; team_id?: string | null;
}
export interface McpServer {
  name: string; enabled: boolean; transport: string; host: string | null; trusted_tools: string[];
  status?: "ok" | "unreachable" | "disabled"; error?: string; available_tools?: string[];
  missing_tools?: string[]; ignored_tools?: string[];
}
export interface PolicyTool extends CatalogTool { mode: "default" | "approval_required" | "disabled"; effective: string; configurable: boolean }
export interface TeamMember { user_id: string; email: string; role: string }
export interface Team { id: string; name: string; created_at: string; role: string | null; members: TeamMember[] }
export interface Change { evidence_id: string; step: string; path: string; change_type: string; rationale: string; diff: string }
export interface PullRequestProposal {
  id: string; run_id: string; status: "awaiting_confirmation" | "creating" | "created" | "failed" | "cancelled";
  title: string; body: string; branch: string; base_sha: string; diff: string; content_hash: string;
  files: { path: string; change_type: string; rationale: string }[];
  pr_number: number | null; pr_url: string | null; error: string | null; created_at: string;
}
export interface PlatformConfig {
  llm_provider: string; llm_model: string; llm_fallback_model: string | null;
  private_repositories: boolean; read_only: boolean; limits: Limits;
  pull_requests?: { enabled: boolean; confirmation_required: boolean }; workflows?: WorkflowTemplate[];
  test_runner: { enabled: boolean; install_network: boolean; timeout_seconds: number; approval_required: boolean };
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
const send = <T,>(method: string, path: string, body?: unknown) =>
  request<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

/** Authenticated file download (PDF, Markdown, JSON exports). */
export async function download(path: string, filename: string): Promise<void> {
  const response = await fetch(BASE + path, { headers: authHeaders() });
  if (!response.ok) throw new ApiError(response.status, "DOWNLOAD_FAILED", `The server answered ${response.status}.`);
  const url = URL.createObjectURL(await response.blob());
  const link = Object.assign(document.createElement("a"), { href: url, download: filename });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface StartRunInput {
  project_id: string; objective?: string; workflow_type?: string; workflow_id?: string;
  run_tests?: boolean; propose_fixes?: boolean;
}

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
  startRun: ({ project_id, objective, workflow_type, workflow_id, run_tests = false, propose_fixes = false }: StartRunInput) =>
    post<{ run_id: string; status: RunStatus }>("/runs", {
      project_id, ...(objective ? { objective } : {}), ...(workflow_id ? { workflow_id } : { workflow_type }),
      options: { run_tests, propose_fixes },
    }, { "Idempotency-Key": crypto.randomUUID() }),
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
  servers: (probe = false) => request<{ servers: McpServer[] }>(`/mcp/servers${probe ? "?probe=true" : ""}`),
  policies: (projectId: string) => request<{ tools: PolicyTool[] }>(`/projects/${projectId}/tool-policies`),
  setPolicy: (projectId: string, tool: string, mode: string) =>
    send<{ tools: PolicyTool[] }>("PUT", `/projects/${projectId}/tool-policies`, { tool, mode }),
  updateProject: (projectId: string, body: { name?: string; github_token?: string; github_write_token?: string; team_id?: string; clear_team?: boolean }) =>
    send<Project>("PATCH", `/projects/${projectId}`, body),
  runMetrics: (id: string) => request<RunMetrics>(`/runs/${id}/metrics`),
  workflows: () => request<{ builtin: WorkflowTemplate[]; saved: WorkflowTemplate[] }>("/workflows"),
  createWorkflow: (body: { name: string; base_type: string; objective_template: string; description?: string; agents?: string[]; run_tests?: boolean; propose_fixes?: boolean; team_id?: string }) =>
    post<WorkflowTemplate>("/workflows", body),
  deleteWorkflow: (id: string) => send<void>("DELETE", `/workflows/${id}`),
  teams: () => request<Team[]>("/teams"),
  createTeam: (name: string) => post<Team>("/teams", { name }),
  addMember: (teamId: string, email: string, role: string) => post<Team>(`/teams/${teamId}/members`, { email, role }),
  removeMember: (teamId: string, userId: string) => send<Team>("DELETE", `/teams/${teamId}/members/${userId}`),
  changes: (runId: string) => request<{ changes: Change[] }>(`/runs/${runId}/changes`),
  pullRequests: (runId: string) => request<PullRequestProposal[]>(`/runs/${runId}/pull-requests`),
  preparePullRequest: (runId: string, body: { title?: string; include_report?: boolean; evidence_ids?: string[] }) =>
    post<PullRequestProposal>(`/runs/${runId}/pull-requests`, body),
  confirmPullRequest: (id: string, content_hash: string) => post<PullRequestProposal>(`/pull-requests/${id}/confirm`, { content_hash }),
  cancelPullRequest: (id: string) => post<PullRequestProposal>(`/pull-requests/${id}/cancel`),
};
