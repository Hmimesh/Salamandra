import { useEffect, useRef, useState, type FormEvent } from "react";
import { Plus, Pencil, X } from "lucide-react";
import { Modal } from "./ui";
import { apiRequest } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";

export type CrewProfile = { id: string; version: number; name: string; kind: string; active: boolean; complexity: number; contact: string; notes: string; skills: Record<string, number> };
export function SkillFields({ value, onChange }: { value: [string, number][]; onChange: (value: [string, number][]) => void }) {
  return <fieldset className="crew-skills"><legend>Skills</legend>{value.map(([name, level], index) => <div className="crew-skill-row" key={index}>
    <input aria-label={`Skill ${index + 1}`} dir="auto" value={name} maxLength={100} required onChange={e => onChange(value.map((row, i) => i === index ? [e.target.value, level] : row))} />
    <select aria-label={`Skill ${index + 1} proficiency`} value={level} onChange={e => onChange(value.map((row, i) => i === index ? [name, Number(e.target.value)] : row))}><option value={1}>Basic</option><option value={2}>Intermediate</option><option value={3}>Advanced</option></select>
    <button type="button" className="icon-button" aria-label={`Remove skill ${index + 1}`} title="Remove skill" onClick={() => onChange(value.filter((_, i) => i !== index))}><X size={16} /></button>
  </div>)}<button type="button" className="button button-secondary" disabled={value.length >= 50} onClick={() => onChange([...value, ["", 1]])}><Plus size={16} />Add skill</button></fieldset>;
}

export function CrewProfiles() {
  const { state } = useWorkspace();
  const [profiles, setProfiles] = useState<CrewProfile[]>([]);
  const [editing, setEditing] = useState<Partial<CrewProfile> | null>(null);
  const [skills, setSkills] = useState<[string, number][]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const retry = useRef({ payload: "", key: "" });
  const canManage = ["owner", "admin", "operator"].includes(state!.auth.user!.role);
  useEffect(() => { let live = true; void apiRequest<{ profiles: CrewProfile[] }>("/api/crew").then(r => { if (live) setProfiles(r.profiles); }).catch(e => { if (live) setError(String(e.message)); }); return () => { live = false; }; }, [revision]);
  function edit(profile: Partial<CrewProfile>) { setEditing(profile); setSkills(Object.entries(profile.skills || {})); setError(""); }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return;
    const form = new FormData(event.currentTarget);
    const body = { ...(editing?.id ? { id: editing.id, version: editing.version } : {}), name: form.get("name"), kind: form.get("kind"), active: form.get("active") === "on", complexity: Number(form.get("complexity")), contact: form.get("contact"), notes: form.get("notes"), skills: Object.fromEntries(skills) };
    const payload = JSON.stringify(body); if (retry.current.payload !== payload) retry.current = { payload, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try { await apiRequest("/api/crew", { method: "POST", body: { ...body, idempotency_key: retry.current.key } }); setEditing(null); setRevision(v => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "Crew profile could not be saved."); } finally { setBusy(false); }
  }
  return <section className="data-section" aria-label="Crew profiles"><div className="section-title-row"><h2>Crew profiles</h2>{canManage ? <button className="button button-secondary" onClick={() => edit({ active: true, kind: "external", complexity: 1 })}><Plus size={16} />Add crew profile</button> : null}</div>
    {error && !editing ? <p role="alert">{error}</p> : null}{!profiles.length ? <p>No crew profiles yet.</p> : profiles.map(profile => <div className="crew-profile-row" key={profile.id}><div><strong dir="auto">{profile.name}</strong><p>{profile.kind === "external" ? "External" : "Internal"} · {profile.active ? "Active" : "Inactive"}</p><small dir="auto">{Object.keys(profile.skills).join(" · ")}</small></div>{canManage ? <button className="icon-button" aria-label={`Edit ${profile.name}`} title="Edit crew profile" onClick={() => edit(profile)}><Pencil size={16} /></button> : null}</div>)}
    <Modal open={editing !== null} title={editing?.id ? "Edit crew profile" : "Add crew profile"} onClose={() => { if (!busy) setEditing(null); }}>
      <form className="form-stack" onSubmit={save}><fieldset disabled={busy} className="crew-form"><div className="form-grid"><label>Name<input name="name" dir="auto" defaultValue={editing?.name} maxLength={200} required /></label><label>Type<select name="kind" defaultValue={editing?.kind}><option value="external">External / freelancer</option><option value="internal">Internal</option></select></label><label>Complexity<select name="complexity" defaultValue={editing?.complexity}><option value={1}>Basic</option><option value={2}>Intermediate</option><option value={3}>Advanced</option></select></label><label>Contact<input name="contact" dir="auto" maxLength={400} defaultValue={editing?.contact} /></label></div>
        <label className="checkbox-field"><input name="active" type="checkbox" defaultChecked={editing?.active} />Active</label><label>Operational notes<textarea name="notes" dir="auto" maxLength={2000} defaultValue={editing?.notes} /></label><SkillFields value={skills} onChange={setSkills} />
        {error ? <p role="alert">{error}</p> : null}<div className="modal-actions"><button className="button button-secondary" type="button" onClick={() => setEditing(null)}>Cancel</button><button className="button button-primary">Save crew profile</button></div></fieldset></form>
    </Modal></section>;
}
