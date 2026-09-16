import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Pencil, Plus, ShieldX } from "lucide-react";
import { apiRequest } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import { SkillFields, type CrewProfile } from "./CrewProfiles";
import type { EventRecord } from "../types";

type CrewRole = { id: string; version: number; name: string; quantity: number; complexity: number; skills: Record<string, number> };
type Assignment = { id: string; version: number; profile_id: string; role_id: string; call_at: string; release_at: string; status: string; notes: string; equipment: string[] };
type Crew = { event_version: number; roles: CrewRole[]; assignments: Assignment[]; equipment: { id: string; name: string; quantity: number }[] };
function local(value: string) { return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Jerusalem", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)).replace(" ", "T"); }

export function EventCrew({ event }: { event: EventRecord }) {
  const { state, refresh } = useWorkspace();
  const [crew, setCrew] = useState<Crew | null>(null);
  const [profiles, setProfiles] = useState<CrewProfile[]>([]);
  const [form, setForm] = useState<"role" | "assignment" | null>(null);
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [skills, setSkills] = useState<[string, number][]>([]);
  const [equipment, setEquipment] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const retry = useRef({ payload: "", key: "" });
  const role = state!.auth.user!.role;
  const privileged = ["owner", "admin", "operator"].includes(role);
  const closed = ["returned", "cancelled"].includes(event.status);
  useEffect(() => {
    let live = true;
    void Promise.all([apiRequest<Crew>(`/api/events/crew?event_id=${encodeURIComponent(event.id)}`), apiRequest<{ profiles: CrewProfile[] }>("/api/crew")])
      .then(([data, people]) => { if (live) { setCrew(data); setProfiles(people.profiles); } })
      .catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [event.id, event.version, revision]);
  async function command(path: string, body: Record<string, unknown>) {
    const payload = JSON.stringify({ path, body });
    if (retry.current.payload !== payload) retry.current = { payload, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      const result = await apiRequest<{ token?: string | null }>(path, { method: "POST", body: { ...body, idempotency_key: retry.current.key } });
      setForm(null); setRevision(v => v + 1); await refresh();
      return result;
    } catch (e) { setError(e instanceof Error ? e.message : "Crew changes could not be saved."); return null; }
    finally { setBusy(false); }
  }
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (!crew || busy) return;
    const values = new FormData(e.currentTarget);
    const base = { event_id: event.id, event_version: crew.event_version };
    if (form === "role") await command("/api/events/crew/role", { ...base, name: values.get("name"), quantity: Number(values.get("quantity")), complexity: Number(values.get("complexity")), skills: Object.fromEntries(skills) });
    else await command("/api/events/crew/assign", { ...base, ...(assignment ? { id: assignment.id, version: assignment.version } : {}),
      profile_id: assignment?.profile_id || values.get("person"), role_id: assignment?.role_id || values.get("role"),
      call_at: values.get("call"), release_at: values.get("release"), status: values.get("status") || "assigned", notes: values.get("notes"),
      equipment, override_reason: values.get("override") || "" });
  }
  function edit(value: Assignment | null) { setAssignment(value); setEquipment(value?.equipment || []); setForm("assignment"); setError(""); setLink(""); }
  async function access(value: Assignment, action: "issue" | "revoke") {
    setLink("");
    const result = await command("/api/events/crew/access", { assignment_id: value.id, version: value.version, action });
    if (result?.token) setLink(`${location.origin}/external#${result.token}`);
    else if (action === "issue" && result) setError("This link was already issued. Regenerate it to display a new link.");
  }
  return <section aria-label="Event crew assignments"><div className="section-title-row"><h3>Crew</h3>{!closed && !form ? <div className="row-actions"><button className="button button-secondary" disabled={!crew || busy} onClick={() => { setSkills([]); setForm("role"); }}><Plus size={16} />Add role</button><button className="button button-secondary" disabled={!crew?.roles.length || !profiles.some(p => p.active) || busy} onClick={() => edit(null)}><Plus size={16} />Assign crew</button></div> : null}</div>
    {error ? <p role="alert">{error}</p> : null}{link ? <label>Assignment link<input aria-label="Assignment link" readOnly value={link} onFocus={e => e.target.select()} /><small>Anyone with this link can view this assignment. Regenerating revokes the previous link.</small></label> : null}
    {form ? <form className="form-stack" onSubmit={save}><fieldset className="crew-form" disabled={busy}>
      {form === "role" ? <><div className="form-grid"><label>Role name<input name="name" dir="auto" required maxLength={200} /></label><label>People needed<input name="quantity" type="number" min={1} max={100} defaultValue={1} required /></label><label>Required complexity<select name="complexity"><option value={1}>Basic</option><option value={2}>Intermediate</option><option value={3}>Advanced</option></select></label></div><SkillFields value={skills} onChange={setSkills} /></> : <>
        <div className="form-grid"><label>Person<select name="person" defaultValue={assignment?.profile_id} disabled={!!assignment} required><option value="">Choose person</option>{profiles.filter(p => p.active || p.id === assignment?.profile_id).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>Event role<select name="role" defaultValue={assignment?.role_id} disabled={!!assignment} required><option value="">Choose role</option>{crew?.roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
        <label>Call time (Asia/Jerusalem)<input type="datetime-local" name="call" defaultValue={assignment ? local(assignment.call_at) : `${event.start_date}T${event.start_time}`} required /></label><label>Release time (Asia/Jerusalem)<input type="datetime-local" name="release" defaultValue={assignment ? local(assignment.release_at) : ""} required /></label>
        {assignment ? <label>Assignment status<select name="status" defaultValue={assignment.status}><option value="assigned">Assigned</option><option value="cancelled">Cancelled</option></select></label> : null}</div>
        <label>Notes for this person<textarea name="notes" dir="auto" maxLength={2000} defaultValue={assignment?.notes} /></label>
        <fieldset className="crew-skills"><legend>Equipment visible to this person</legend>{crew?.equipment.length ? crew.equipment.map(item => <label className="checkbox-field" key={item.id}><input type="checkbox" checked={equipment.includes(item.id)} onChange={e => setEquipment(current => e.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} /><span dir="auto">{item.name} · {item.quantity}</span></label>) : <p>No allocated equipment.</p>}</fieldset>
        {privileged ? <label>Conflict override reason (only when needed)<textarea name="override" dir="auto" maxLength={1000} /></label> : null}
      </>}
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setForm(null)}>Cancel</button><button className="button button-primary">{form === "role" ? "Save role" : "Save assignment"}</button></div>
    </fieldset></form> : <>
      {!crew?.roles.length ? <p>No crew roles yet.</p> : crew.roles.map(r => <p key={r.id} dir="auto"><strong>{r.quantity} × {r.name}</strong> · {crew.assignments.filter(a => a.role_id === r.id && a.status === "assigned").length} assigned</p>)}
      {crew?.assignments.map(a => <div className="crew-profile-row" key={a.id}><div><strong dir="auto">{profiles.find(p => p.id === a.profile_id)?.name || "Crew member"}</strong><p dir="auto">{crew.roles.find(r => r.id === a.role_id)?.name} · {a.status}</p><small>{local(a.call_at).replace("T", " ")} – {local(a.release_at).replace("T", " ")}</small><p dir="auto">{a.notes}</p></div><div className="row-actions">
        {!closed && a.status === "assigned" ? <button className="icon-button" disabled={busy} aria-label="Edit assignment" title="Edit assignment" onClick={() => edit(a)}><Pencil size={16} /></button> : null}
        {privileged && a.status === "assigned" && profiles.find(p => p.id === a.profile_id)?.kind === "external" ? <>{!closed ? <button className="icon-button" disabled={busy} aria-label="Issue or regenerate assignment link" title="Issue or regenerate assignment link" onClick={() => void access(a, "issue")}><Link size={16} /></button> : null}<button className="icon-button" disabled={busy} aria-label="Revoke assignment links" title="Revoke assignment links" onClick={() => void access(a, "revoke")}><ShieldX size={16} /></button></> : null}
      </div></div>)}
    </>}
  </section>;
}
