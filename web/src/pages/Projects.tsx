import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Page } from "../components/AppShell";
import { Button, Empty, ErrorNote, Eyebrow, Field, INPUT, Panel, Skeleton, Tag } from "../components/ui";
import { api } from "../lib/api";
import { dayTime } from "../lib/format";

export function Projects() {
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const create = useMutation({
    mutationFn: () => api.createProject(name.trim() || url.trim().split("/").filter(Boolean).pop()!.replace(/\.git$/, ""), url.trim()),
    onSuccess: () => {
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
          {projects.data?.items.length === 0 && <Empty title="no repository yet.">Add a public GitHub repository to start a read-only analysis.</Empty>}
          <ul>
            {projects.data?.items.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-4 border-b border-line px-6 py-4 last:border-b-0">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{p.name}</p>
                  <p className="mt-1.5 font-mono text-xs text-muted">{p.repo_owner}/{p.repo_name} · added {dayTime(p.created_at)}</p>
                </div>
                <Tag tone="teal">Read-only</Tag>
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
            {create.isError && <ErrorNote error={create.error} />}
            <Button type="submit" disabled={create.isPending} className="w-full py-3">{create.isPending ? "Adding…" : "Add project"}</Button>
            <p className="text-[13px] leading-relaxed text-muted">
              The repository is read through a GET-only connector. Private repositories need a server-side GitHub token.
            </p>
          </form>
        </Panel>
      </div>
    </Page>
  );
}
