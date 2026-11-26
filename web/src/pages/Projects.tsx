import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Page } from "../components/AppShell";
import { Button, Empty, ErrorNote, Eyebrow, Field, INPUT, Panel, Skeleton, Tag } from "../components/ui";
import { api, type Project } from "../lib/api";
import { dayTime } from "../lib/format";

// Write token and team sharing (F-12, F-13). Tokens are write-only: they are never shown back.
function ProjectSettings({ project, onDone }: { project: Project; onDone: () => void }) {
  const client = useQueryClient();
  const teams = useQuery({ queryKey: ["teams"], queryFn: api.teams });
  const [writeToken, setWriteToken] = useState("");
  const [team, setTeam] = useState(project.team_id ?? "");
  const save = useMutation({
    mutationFn: () => api.updateProject(project.id, {
      ...(writeToken.trim() ? { github_write_token: writeToken.trim() } : {}),
      ...(team ? { team_id: team } : { clear_team: !!project.team_id }),
    }),
    onSuccess: () => { setWriteToken(""); void client.invalidateQueries({ queryKey: ["projects"] }); onDone(); },
  });
  const clearToken = useMutation({
    mutationFn: () => api.updateProject(project.id, { github_write_token: "" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["projects"] }),
  });
  const manageable = (teams.data ?? []).filter((t) => t.role === "owner" || t.role === "admin");
  return (
    <form className="mt-4 basis-full space-y-3 rounded-[5px] border border-line p-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Field label="Share with a team" hint="Team members get access according to their team role.">
        <select className={INPUT} value={team} onChange={(e) => setTeam(e.target.value)}>
          <option value="">Not shared</option>
          {manageable.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </Field>
      <Field label={project.has_write_token ? "Write token — set (enter a new one to replace it)" : "Write token — for pull requests"}
        hint="Fine-grained token with Contents and Pull requests: write, on this repository only. Used only when you confirm a pull request.">
        <input className={`${INPUT} font-mono`} type="password" autoComplete="off" maxLength={400} placeholder="github_pat_…"
          value={writeToken} onChange={(e) => setWriteToken(e.target.value)} />
      </Field>
      {(save.isError || clearToken.isError) && <ErrorNote error={save.error ?? clearToken.error} />}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={save.isPending}>Save</Button>
        {project.has_write_token && <Button variant="danger" onClick={() => clearToken.mutate()}>Remove write token</Button>}
        <Button variant="secondary" onClick={onDone}>Close</Button>
      </div>
    </form>
  );
}

export function Projects() {
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const config = useQuery({ queryKey: ["config"], queryFn: api.config, staleTime: 60_000 });
  const create = useMutation({
    mutationFn: () => api.createProject(name.trim() || url.trim().split("/").filter(Boolean).pop()!.replace(/\.git$/, ""), url.trim(), token.trim() || undefined),
    onSuccess: () => {
      setToken("");
      setName("");
      setUrl("");
      void client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (url.trim()) create.mutate();
  };
  return (
    <Page crumb="Projects" title="projects." subtitle="GitHub repositories agents are allowed to read.">
      <div className="grid items-start gap-7 xl:grid-cols-[1.45fr_1fr]">
        <Panel>
          <div className="border-b border-line px-6 py-5"><h2 className="font-medium">Connected repositories</h2></div>
          {projects.isPending && <div className="space-y-3 p-6"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>}
          {projects.isError && <div className="p-6"><ErrorNote error={projects.error} retry={() => void projects.refetch()} /></div>}
          {projects.data?.items.length === 0 && <Empty title="no repository yet.">Add a GitHub repository, public or private, to start a read-only analysis.</Empty>}
          <ul>
            {projects.data?.items.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-4 border-b border-line px-6 py-4 last:border-b-0">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{p.name}</p>
                  <p className="mt-1.5 font-mono text-xs text-muted">{p.repo_owner}/{p.repo_name} · added {dayTime(p.created_at)}</p>
                </div>
                {p.has_token && <Tag>Private · token</Tag>}
                {p.team_id && <Tag tone="violet">Team</Tag>}
                {p.has_write_token ? <Tag tone="amber">PRs · confirmation</Tag> : <Tag tone="teal">Read-only</Tag>}
                {(p.role ?? "owner") !== "viewer" && (
                  <button type="button" className="text-sm text-violet-soft underline underline-offset-4" onClick={() => setEditing(editing === p.id ? null : p.id)}>
                    settings
                  </button>
                )}
                {editing === p.id && <ProjectSettings project={p} onDone={() => setEditing(null)} />}
              </li>
            ))}
          </ul>
        </Panel>
        <Panel className="p-6">
          <Eyebrow>Add a project</Eyebrow>
          <h2 className="mt-3 font-display text-2xl tracking-tight">connect a repository.</h2>
          <form onSubmit={submit} className="mt-6 space-y-5">
            <Field label="GitHub repository" hint="https://github.com/owner/repo or owner/repo">
              <input className={`${INPUT} font-mono`} required placeholder="owner/repo" value={url} onChange={(e) => setUrl(e.target.value)} />
            </Field>
            <Field label="Display name" hint="Optional. Defaults to the repository name.">
              <input className={INPUT} maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Access token — private repositories only"
              hint={config.data && !config.data.private_repositories
                ? "Unavailable: SECRETS_KEY is not configured on the server."
                : "Fine-grained GitHub token with read-only Contents and Metadata. Stored encrypted, never shown again."}>
              <input className={`${INPUT} font-mono`} type="password" autoComplete="off" minLength={20} maxLength={400}
                disabled={config.data ? !config.data.private_repositories : false}
                placeholder="github_pat_…" value={token} onChange={(e) => setToken(e.target.value)} />
            </Field>
            {create.isError && <ErrorNote error={create.error} />}
            <Button type="submit" disabled={create.isPending} className="w-full py-3">{create.isPending ? "Adding…" : "Add project"}</Button>
            <p className="text-[13px] leading-relaxed text-muted">
              The repository is read through a GET-only connector. Its code is sent to the configured model provider during a run.
            </p>
          </form>
        </Panel>
      </div>
    </Page>
  );
}
