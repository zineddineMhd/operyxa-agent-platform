import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth";
import { Page } from "../components/AppShell";
import { Button, ErrorNote, Eyebrow, KeyValue, Panel, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { duration, money } from "../lib/format";

export function Settings() {
  const { user, signOut } = useAuth();
  const config = useQuery({ queryKey: ["config"], queryFn: api.config });
  const c = config.data;
  return (
    <Page crumb="Settings" title="settings." subtitle="Model, run limits, account and security posture.">
      {config.isError && <div className="mb-6"><ErrorNote error={config.error} retry={() => void config.refetch()} /></div>}
      <div className="grid items-start gap-7 lg:grid-cols-2">
        <Panel className="p-6">
          <Eyebrow>Model</Eyebrow>
          {!c ? <Skeleton className="mt-4 h-24" /> : (
            <dl className="mt-3">
              <KeyValue label="Provider">{c.llm_provider}</KeyValue>
              <KeyValue label="Model">{c.llm_model}</KeyValue>
              <KeyValue label="Agent access">read-only</KeyValue>
            </dl>
          )}
          {c?.llm_provider === "demo" && (
            <p className="mt-4 text-sm leading-relaxed text-muted">
              The demo model is deterministic and offline: it follows fixed heuristics instead of reasoning. Set
              <code className="mx-1 font-mono text-xs text-fg">LLM_PROVIDER=gemini</code>on the server to use Gemini.
            </p>
          )}
        </Panel>
        <Panel className="p-6">
          <Eyebrow>Run limits</Eyebrow>
          {!c ? <Skeleton className="mt-4 h-24" /> : (
            <dl className="mt-3">
              <KeyValue label="Agent steps per run">{c.limits.max_llm_calls}</KeyValue>
              <KeyValue label="Tool calls per run">{c.limits.max_tool_calls}</KeyValue>
              <KeyValue label="Run time">{duration(c.limits.max_seconds)}</KeyValue>
              <KeyValue label="Estimated cost per run">{money(c.limits.max_cost_usd)}</KeyValue>
            </dl>
          )}
          <p className="mt-4 text-sm leading-relaxed text-muted">Limits are set per deployment and frozen on each run when it starts.</p>
        </Panel>
        <Panel className="p-6">
          <Eyebrow>Account</Eyebrow>
          <dl className="mt-3">
            <KeyValue label="Email">{user?.email}</KeyValue>
            <KeyValue label="Role">{user?.role}</KeyValue>
          </dl>
          <Button variant="secondary" className="mt-5" onClick={signOut}>Sign out</Button>
        </Panel>
        <Panel className="p-6">
          <Eyebrow>Security posture</Eyebrow>
          <dl className="mt-3">
            <KeyValue label="Repository scope" mono={false}>Injected by the server</KeyValue>
            <KeyValue label="Secrets in tool output" mono={false}>Redacted before storage and model</KeyValue>
            <KeyValue label="Sensitive files" mono={false}>Never read</KeyValue>
            <KeyValue label="Tool calls" mono={false}>Audited</KeyValue>
          </dl>
        </Panel>
      </div>
    </Page>
  );
}
