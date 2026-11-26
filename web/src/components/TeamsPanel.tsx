import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, type Team } from "../lib/api";
import { Button, ErrorNote, Eyebrow, INPUT, Panel, Tag } from "./ui";

const ROLE_HELP = "viewer: read · member: run and approve · admin: manage tokens, policies and pull requests";

function TeamCard({ team }: { team: Team }) {
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const refresh = (data: Team) => client.setQueryData<Team[]>(["teams"], (old) => old?.map((t) => (t.id === data.id ? data : t)));
  const add = useMutation({ mutationFn: () => api.addMember(team.id, email.trim(), role), onSuccess: (d) => { refresh(d); setEmail(""); } });
  const remove = useMutation({ mutationFn: (userId: string) => api.removeMember(team.id, userId), onSuccess: refresh });
  const manager = team.role === "owner" || team.role === "admin";
  const submit = (e: FormEvent) => { e.preventDefault(); if (email.trim()) add.mutate(); };
  return (
    <div className="mt-4 rounded-[5px] border border-line p-4">
      <div className="flex items-center justify-between"><p className="font-medium">{team.name}</p><Tag>{team.role ?? "—"}</Tag></div>
      <ul className="mt-3">
        {team.members.map((m) => (
          <li key={m.user_id} className="flex items-center justify-between gap-3 border-b border-line py-2 text-[13px] last:border-b-0">
            <span className="font-mono">{m.email}</span>
            <span className="flex items-center gap-3 text-muted">{m.role}
              {manager && m.role !== "owner" && <button type="button" className="text-red underline underline-offset-4" onClick={() => remove.mutate(m.user_id)}>remove</button>}
            </span>
          </li>
        ))}
      </ul>
      {manager && (
        <form onSubmit={submit} className="mt-3 flex flex-wrap gap-2">
          <input className={`${INPUT} min-w-0 flex-1`} type="email" placeholder="colleague@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <select className={`${INPUT} w-auto`} value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="viewer">viewer</option><option value="member">member</option><option value="admin">admin</option>
          </select>
          <Button type="submit" variant="secondary" disabled={add.isPending}>Add</Button>
        </form>
      )}
      {(add.isError || remove.isError) && <div className="mt-3"><ErrorNote error={add.error ?? remove.error} /></div>}
    </div>
  );
}

export function TeamsPanel() {
  const client = useQueryClient();
  const teams = useQuery({ queryKey: ["teams"], queryFn: api.teams });
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.createTeam(name.trim()),
    onSuccess: () => { setName(""); void client.invalidateQueries({ queryKey: ["teams"] }); },
  });
  return (
    <Panel className="p-6">
      <Eyebrow>Teams</Eyebrow>
      <p className="mt-3 text-sm leading-relaxed text-muted">Share a project with a team from the Projects page. {ROLE_HELP}. Members must have an account.</p>
      <form className="mt-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
        <input className={`${INPUT} flex-1`} placeholder="Team name" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" disabled={create.isPending}>Create</Button>
      </form>
      {create.isError && <div className="mt-3"><ErrorNote error={create.error} /></div>}
      {teams.data?.map((t) => <TeamCard key={t.id} team={t} />)}
    </Panel>
  );
}

export function WorkflowsPanel() {
  const client = useQueryClient();
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: api.workflows });
  const [name, setName] = useState("");
  const [base, setBase] = useState("repository_analysis");
  const [objective, setObjective] = useState("");
  const create = useMutation({
    mutationFn: () => api.createWorkflow({ name: name.trim(), base_type: base, objective_template: objective.trim() }),
    onSuccess: () => { setName(""); setObjective(""); void client.invalidateQueries({ queryKey: ["workflows"] }); },
  });
  const remove = useMutation({ mutationFn: api.deleteWorkflow, onSuccess: () => void client.invalidateQueries({ queryKey: ["workflows"] }) });
  return (
    <Panel className="p-6">
      <Eyebrow>Saved workflows</Eyebrow>
      <p className="mt-3 text-sm leading-relaxed text-muted">A reusable mission on top of a built-in workflow. Use <code className="font-mono text-xs">{"{repository}"}</code> in the objective.</p>
      <form className="mt-4 space-y-2" onSubmit={(e) => { e.preventDefault(); if (name.trim() && objective.trim().length >= 10) create.mutate(); }}>
        <div className="flex gap-2">
          <input className={`${INPUT} flex-1`} placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <select className={`${INPUT} w-auto`} value={base} onChange={(e) => setBase(e.target.value)}>
            {workflows.data?.builtin.map((w) => <option key={w.key} value={w.key}>{w.name}</option>)}
          </select>
        </div>
        <textarea className={`${INPUT} min-h-[72px] font-mono`} placeholder="Check the dependencies of {repository}…" value={objective} onChange={(e) => setObjective(e.target.value)} />
        <Button type="submit" variant="secondary" disabled={create.isPending}>Save workflow</Button>
      </form>
      {create.isError && <div className="mt-3"><ErrorNote error={create.error} /></div>}
      <ul className="mt-4">
        {workflows.data?.saved.map((w) => (
          <li key={w.id} className="flex items-center justify-between gap-3 border-b border-line py-2 text-[13px] last:border-b-0">
            <span><span className="font-medium">{w.name}</span> <span className="text-muted">· {w.base_type} · {w.agents.join(", ")}</span></span>
            <button type="button" className="text-red underline underline-offset-4" onClick={() => remove.mutate(w.id!)}>delete</button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
