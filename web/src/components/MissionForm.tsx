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
  const [proposeFixes, setProposeFixes] = useState(false);
  const [workflow, setWorkflow] = useState("builtin:repository_analysis");
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: api.workflows, staleTime: 60_000 });
  const testRunner = config.data?.test_runner;
  const pullRequests = config.data?.pull_requests?.enabled ?? false;
  const selected = projectId || projects.data?.items[0]?.id || "";
  const project = projects.data?.items.find((p) => p.id === selected);
  const [kind, key] = workflow.split(":");
  const template = kind === "builtin"
    ? workflows.data?.builtin.find((w) => w.key === key)
    : workflows.data?.saved.find((w) => w.id === key);
  const placeholder = template && project
    ? template.objective_template.replace("{repository}", `${project.repo_owner}/${project.repo_name}`)
    : EXAMPLE;
  const chooseWorkflow = (value: string) => {
    setWorkflow(value);
    const [k, id] = value.split(":");
    const next = k === "builtin" ? workflows.data?.builtin.find((w) => w.key === id) : workflows.data?.saved.find((w) => w.id === id);
    setRunTests(!!next?.options.run_tests);
    setProposeFixes(!!next?.options.propose_fixes);
  };

  const start = useMutation({
    mutationFn: () => api.startRun({
      project_id: selected,
      objective: objective.trim() || undefined,
      ...(kind === "builtin" ? { workflow_type: key } : { workflow_id: key }),
      run_tests: runTests && !!testRunner?.enabled,
      propose_fixes: proposeFixes && pullRequests,
    }),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["runs"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
      navigate(`/app/studio/${run.run_id}`);
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (selected && (objective.trim().length >= 10 || (!!template && objective.trim().length === 0))) start.mutate();
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
          <Field label="Workflow" hint={template?.description}>
            <select className={INPUT} value={workflow} onChange={(e) => chooseWorkflow(e.target.value)}>
              {workflows.data?.builtin.map((w) => <option key={w.key} value={`builtin:${w.key}`}>{w.name}</option>)}
              {!!workflows.data?.saved.length && (
                <optgroup label="Saved workflows">
                  {workflows.data.saved.map((w) => <option key={w.id} value={`saved:${w.id}`}>{w.name}</option>)}
                </optgroup>
              )}
            </select>
          </Field>
          <Field label="Mission" hint="Leave empty to use the workflow's objective. Agents can only read.">
            <textarea className={`${INPUT} min-h-[132px] resize-y font-mono leading-relaxed`} minLength={10}
              maxLength={4000} placeholder={placeholder} value={objective} onChange={(e) => setObjective(e.target.value)} />
          </Field>
          {pullRequests && (
            <label className="flex cursor-pointer items-start gap-3 rounded-[5px] border border-line p-3.5 text-sm">
              <input type="checkbox" className="mt-0.5 accent-current" checked={proposeFixes} onChange={(e) => setProposeFixes(e.target.checked)} />
              <span>
                <span className="font-medium">Propose fixes</span>
                <span className="mt-1 block text-[13px] leading-relaxed text-muted">
                  A remediation agent drafts small changes. Nothing is written: you review the diff and confirm before a pull request is opened.
                </span>
              </span>
            </label>
          )}
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
            {proposeFixes && pullRequests && <Tag tone="violet">Fixes · confirmation</Tag>}
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
