import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Page } from "../components/AppShell";
import { cx, ErrorNote, Eyebrow, Field, INPUT, Panel, Skeleton, Status } from "../components/ui";
import { api, type CatalogTool } from "../lib/api";

const SERVERS: Record<string, { code: string; name: string }> = {
  github: { code: "GH", name: "GitHub" },
  analysis: { code: "CA", name: "Code Analysis" },
  changes: { code: "FX", name: "Fix proposals" },
  test_runner: { code: "TR", name: "Test Runner" },
};
const MODES: Record<string, string> = { default: "Catalog policy", approval_required: "Require approval", disabled: "Disabled" };
const EFFECTIVE: Record<string, string> = { allow: "Allowed", approval_required: "Approval", deny: "Denied" };

function ProjectPolicies() {
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [chosen, setChosen] = useState("");
  const projectId = chosen || projects.data?.items[0]?.id || "";
  const policies = useQuery({ queryKey: ["policies", projectId], queryFn: () => api.policies(projectId), enabled: !!projectId });
  const change = useMutation({
    mutationFn: ({ tool, mode }: { tool: string; mode: string }) => api.setPolicy(projectId, tool, mode),
    onSuccess: (data) => client.setQueryData(["policies", projectId], data),
  });
  if (projects.isSuccess && projects.data.items.length === 0) return null;
  return (
    <Panel className="mt-7 overflow-x-auto">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-line px-6 py-5">
        <div>
          <h2 className="font-medium">Project policies</h2>
          <p className="mt-1.5 max-w-xl text-sm text-muted">
            Switch a tool off or require a human approval for one project. An override can only make the catalog stricter;
            runs keep the policy they started with.
          </p>
        </div>
        <Field label="Project">
          <select className={`${INPUT} min-w-[240px] font-mono`} value={projectId} onChange={(e) => setChosen(e.target.value)}>
            {projects.data?.items.map((p) => <option key={p.id} value={p.id}>{p.repo_owner}/{p.repo_name}</option>)}
          </select>
        </Field>
      </div>
      {change.isError && <div className="px-6 pt-4"><ErrorNote error={change.error} /></div>}
      {policies.isPending && <Skeleton className="m-6 h-24" />}
      {policies.isError && <div className="p-6"><ErrorNote error={policies.error} /></div>}
      {policies.data && (
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead>
            <tr className="border-b border-line font-mono text-[11px] uppercase tracking-[0.16em] text-faint">
              <th className={TH}>Tool</th><th className={TH}>Risk</th><th className={TH}>Override</th><th className={TH}>Effective</th>
            </tr>
          </thead>
          <tbody>
            {policies.data.tools.map((tool) => (
              <tr key={tool.tool} className="border-b border-line last:border-b-0">
                <td className="px-6 py-3 font-mono text-[13px]">{tool.tool}</td>
                <td className={cx("px-6 capitalize", RISK[tool.risk])}>{tool.risk}</td>
                <td className="px-6">
                  <select aria-label={`Policy for ${tool.tool}`} className={`${INPUT} py-1.5`} value={tool.mode} disabled={!tool.configurable || change.isPending}
                    onChange={(e) => change.mutate({ tool: tool.tool, mode: e.target.value })}>
                    {Object.entries(MODES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </td>
                <td className={cx("px-6", tool.effective === "deny" ? "text-red" : tool.effective === "approval_required" ? "text-amber" : "text-teal")}>
                  {EFFECTIVE[tool.effective] ?? tool.effective}{!tool.configurable && " · required"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
const RISK: Record<string, string> = { low: "text-teal", moderate: "text-fg", high: "text-amber", critical: "text-red" };
const POLICY: Record<string, string> = { allow: "Allow · read", approval_required: "Approval · sandbox profile", deny: "Disabled" };
const TH = "px-6 py-3.5 font-normal";

function ServerCard({ code, name, tools, status, scope, tone }: { code: string; name: string; tools: string; status: string; scope: string; tone?: "amber" }) {
  return (
    <Panel tone={tone} className="p-6">
      <div className="flex items-center gap-4">
        <span className="grid size-11 place-items-center rounded-[5px] border border-line-strong font-mono text-sm">{code}</span>
        <h2 className="flex-1 text-xl font-medium">{name}</h2>
        <Status status={status} label={status === "restricted" ? "Not enabled" : undefined} />
      </div>
      <div className="mt-6 flex justify-between border-t border-line pt-4 font-mono text-[13px] text-muted">
        <span>{tools}</span><span>Scope: {scope}</span>
      </div>
    </Panel>
  );
}

export function McpHub() {
  const tools = useQuery({ queryKey: ["tools"], queryFn: api.tools });
  const servers = useQuery({ queryKey: ["servers"], queryFn: () => api.servers(true), staleTime: 30_000 });
  const config = useQuery({ queryKey: ["config"], queryFn: api.config, staleTime: 60_000 });
  const testRunner = !!config.data?.test_runner.enabled;
  const health = new Map((servers.data?.servers ?? []).map((s) => [s.name, s]));
  const connected = (server: string) => health.get(server)?.enabled ?? (server !== "test_runner" || testRunner);
  const byServer = new Map<string, CatalogTool[]>();
  // The catalog lists every trusted tool; the test runner is connected only when the server enables it.
  for (const tool of tools.data?.tools ?? []) {
    if (connected(tool.server)) byServer.set(tool.server, [...(byServer.get(tool.server) ?? []), tool]);
  }
  return (
    <Page crumb="MCP Hub" title="mcp hub." subtitle="Connectors, tool permissions and operational health.">
      {tools.isError && <ErrorNote error={tools.error} retry={() => void tools.refetch()} />}
      {tools.isPending && <Skeleton className="h-40" />}
      {tools.data && (
        <>
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {[...byServer].map(([server, list]) => {
              const probe = health.get(server);
              const status = !probe?.status ? "connected" : probe.status === "ok" ? "connected" : probe.status === "unreachable" ? "failed" : "restricted";
              const ignored = probe?.ignored_tools?.length ? ` · ${probe.ignored_tools.length} ignored` : "";
              return (
                <ServerCard key={server} code={SERVERS[server]?.code ?? server.slice(0, 2).toUpperCase()} name={SERVERS[server]?.name ?? server}
                  tools={`${probe?.available_tools?.length ?? list.length} tools${ignored} · ${probe?.transport ?? "in-process"}`} status={status}
                  scope={server === "test_runner" ? "sandbox · approval" : server === "changes" ? "proposals only" : "read-only"}
                  tone={server === "test_runner" ? "amber" : undefined} />
              );
            })}
            {!testRunner && <ServerCard code="TR" name="Test Runner" tools="needs Docker" status="restricted" scope="approval" tone="amber" />}
          </div>
          <div className="mt-7 grid items-start gap-7 xl:grid-cols-[minmax(0,1fr)_360px]">
            <Panel className="overflow-x-auto">
              <div className="border-b border-line px-6 py-5">
                <h2 className="font-medium">Tool permissions</h2>
                <p className="mt-1.5 text-sm text-muted">The trusted catalog: what each agent may execute, and under which policy.</p>
              </div>
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead>
                  <tr className="border-b border-line font-mono text-[11px] uppercase tracking-[0.16em] text-faint">
                    <th className={TH}>Tool</th><th className={TH}>Server</th><th className={TH}>Risk</th><th className={TH}>Policy</th><th className={TH}>Agents</th><th className={TH}>Audit</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.data.tools.map((tool) => (
                    <tr key={tool.tool} className="border-b border-line">
                      <td className="px-6 py-4 font-mono text-[13px]">{tool.tool}</td>
                      <td className="px-6 text-muted">{SERVERS[tool.server]?.name ?? tool.server}</td>
                      <td className={cx("px-6 capitalize", RISK[tool.risk])}>{tool.risk}</td>
                      <td className="px-6">{POLICY[tool.policy] ?? tool.policy}</td>
                      <td className="px-6 text-muted">{connected(tool.server) ? tool.agents.join(", ") : "not enabled"}</td>
                      <td className="px-6 text-muted">Enabled</td>
                    </tr>
                  ))}
                  <tr>
                    <td className="px-6 py-4 font-mono text-[13px] text-muted">arbitrary shell · delete · write</td>
                    <td className="px-6 text-muted">Any agent</td><td className="px-6 text-red">Critical</td>
                    <td className="px-6">Disabled</td><td className="px-6 text-muted">none</td><td className="px-6 text-muted">Enabled</td>
                  </tr>
                </tbody>
              </table>
            </Panel>
            <Panel className="p-6">
              <Eyebrow>Policy reminder</Eyebrow>
              <p className="mt-4 font-display text-[22px] leading-snug tracking-tight">Permissions are enforced by the backend, not by model instructions.</p>
              <p className="mt-4 text-sm leading-relaxed text-muted">
                MCP is a tool interface, not a security policy. Every call goes through the gateway: trusted
                catalog, agent allowlist, parameter validation, repository scope injected by the server, timeout and
                redacted logs. A tool a server advertises but the catalog does not list is ignored.
              </p>
            </Panel>
          </div>
          <ProjectPolicies />
        </>
      )}
    </Page>
  );
}
