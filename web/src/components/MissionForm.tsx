import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { Button, ErrorNote, Eyebrow, Field, INPUT, Panel, Tag, TextLink } from "./ui";

const EXAMPLE = "Review my GitHub repo for security and code quality. Do not write any files.";

export function MissionForm() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const config = useQuery({ queryKey: ["config"], queryFn: api.config, staleTime: 60_000 });
  const [projectId, setProjectId] = useState("");
  const [objective, setObjective] = useState("");
  const [runTests, setRunTests] = useState(false);
  const testRunner = config.data?.test_runner;
  const selected = projectId || projects.data?.items[0]?.id || "";

  const start = useMutation({
    mutationFn: () => api.startRun(selected, objective.trim(), runTests && !!testRunner?.enabled),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["runs"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
      navigate(`/app/studio/${run.run_id}`);
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (selected && objective.trim().length >= 10) start.mutate();
  };
  const noProject = projects.isSuccess && projects.data.items.length === 0;

  return (
    <Panel className="p-6">
      <Eyebrow>Start a mission</Eyebrow>
      <h2 className="mt-3 font-display text-2xl tracking-tight">what should agents do?</h2>
      {noProject ? (
        <p className="mt-5 text-sm leading-relaxed text-muted">
          Connect a GitHub repository first. <TextLink to="/app/projects">Add a project →</TextLink>
        </p>
      ) : (
        <form onSubmit={submit} className="mt-6 space-y-5">
          <Field label="Repository">
            <select className={`${INPUT} font-mono`} value={selected} onChange={(e) => setProjectId(e.target.value)}>
              {projects.data?.items.map((p) => (
                <option key={p.id} value={p.id}>{p.repo_owner}/{p.repo_name}</option>
              ))}
            </select>
          </Field>
          <Field label="Mission" hint="At least 10 characters. Agents can only read.">
            <textarea className={`${INPUT} min-h-[132px] resize-y font-mono leading-relaxed`} required minLength={10}
              maxLength={4000} placeholder={EXAMPLE} value={objective} onChange={(e) => setObjective(e.target.value)} />
          </Field>
          {testRunner?.enabled && (
            <label className="flex cursor-pointer items-start gap-3 rounded-[5px] border border-line p-3.5 text-sm">
              <input type="checkbox" className="mt-0.5 accent-current" checked={runTests} onChange={(e) => setRunTests(e.target.checked)} />
              <span>
                <span className="font-medium">Run the test suite</span>
                <span className="mt-1 block text-[13px] leading-relaxed text-muted">
                  In a throw-away sandbox without network or secrets, limited to {Math.round(testRunner.timeout_seconds / 60)} min.
                  The run pauses until you approve it in the Approval Center.
                </span>
              </span>
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            <Tag tone="teal">Read-only</Tag>
            {runTests && testRunner?.enabled && <Tag tone="amber">Tests · approval</Tag>}
            {config.data && <Tag>{config.data.llm_model.replace(/-/g, " ")}</Tag>}
          </div>
          {start.isError && <ErrorNote error={start.error} />}
          <Button type="submit" disabled={start.isPending || !selected} className="w-full py-3.5 text-[15px]">
            {start.isPending ? "Starting…" : "Analyze repository"}
          </Button>
          <p className="text-[13px] text-muted">The plan is validated on the server before any tool runs.</p>
        </form>
      )}
    </Panel>
  );
}
