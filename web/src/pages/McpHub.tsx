import { useQuery } from "@tanstack/react-query";
import { Page } from "../components/AppShell";
import { cx, ErrorNote, Eyebrow, Panel, Skeleton, Status } from "../components/ui";
import { api, type CatalogTool } from "../lib/api";

const SERVERS: Record<string, { code: string; name: string }> = {
  github: { code: "GH", name: "GitHub" },
  analysis: { code: "CA", name: "Code Analysis" },
  test_runner: { code: "TR", name: "Test Runner" },
};
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
  const byServer = new Map<string, CatalogTool[]>();
  // A server whose tools all need an approval is listed by policy but not connected yet.
  for (const tool of tools.data?.tools ?? []) {
    if (tool.policy === "allow") byServer.set(tool.server, [...(byServer.get(tool.server) ?? []), tool]);
  }
  return (
    <Page crumb="MCP Hub" title="mcp hub." subtitle="Connectors, tool permissions and operational health.">
      {tools.isError && <ErrorNote error={tools.error} retry={() => void tools.refetch()} />}
      {tools.isPending && <Skeleton className="h-40" />}
      {tools.data && (
        <>
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {[...byServer].map(([server, list]) => (
              <ServerCard key={server} code={SERVERS[server]?.code ?? server.slice(0, 2).toUpperCase()} name={SERVERS[server]?.name ?? server}
                tools={`${list.length} tools`} status="connected" scope="read-only" />
            ))}
            <ServerCard code="TR" name="Test Runner" tools="planned · V1" status="restricted" scope="approval" tone="amber" />
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
                      <td className="px-6 text-muted">{tool.policy === "allow" ? tool.agents.join(", ") : "not connected yet"}</td>
                      <td className="px-6 text-muted">Enabled</td>
                    </tr>
                  ))}
                  <tr>
                    <td className="px-6 py-4 font-mono text-[13px] text-muted">arbitrary shell · delete · write</td>
                    <td className="px-6 text-muted">Any</td><td className="px-6 text-red">Critical</td>
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
        </>
      )}
    </Page>
  );
}
