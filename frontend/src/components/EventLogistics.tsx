import { useEffect, useRef, useState, type FormEvent } from "react";
import { Download, Pencil } from "lucide-react";
import { apiRequest } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import type { EventRecord } from "../types";

const milestones = { prepare_at: "Prepare", pack_by: "Pack by", standby_at: "Standby", dispatch_at: "Dispatch", load_in_at: "Load in", setup_at: "Setup", teardown_at: "Teardown", return_due_at: "Return due" };
type Schedule = { event_version: number; timezone: string; milestones: Record<string, string>; notes: string; standby: boolean; reservation_start: string; reservation_end: string };
function localTime(value: string, timezone: string) {
  return value ? new Intl.DateTimeFormat("sv-SE", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)).replace(" ", "T") : "";
}

export function EventLogistics({ event }: { event: EventRecord }) {
  const { state, mutate } = useWorkspace();
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [editing, setEditing] = useState(false);
  const [times, setTimes] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const retry = useRef({ payload: "", key: "" });
  const role = state!.auth.user!.role;
  const canEdit = ["owner", "admin", "operator", "producer"].includes(role);
  const canStage = ["owner", "admin", "operator", "producer", "technician", "freelancer"].includes(role);
  const closed = ["returned", "cancelled"].includes(event.status);
  const currentStep = event.status === "planning" ? "prepare_at" : event.status === "confirmed" ? "pack_by" : event.status === "packed" ? (schedule?.standby ? "standby_at" : "dispatch_at") : event.status === "out" ? "return_due_at" : "";
  useEffect(() => {
    let live = true;
    void apiRequest<Schedule>(`/api/events/logistics?event_id=${encodeURIComponent(event.id)}`)
      .then(result => { if (live) setSchedule(result); })
      .catch(failure => { if (live) setError(failure instanceof Error ? failure.message : "Logistics unavailable."); });
    return () => { live = false; };
  }, [event.id, event.version, attempt]);
  function edit() {
    if (!schedule) return;
    setTimes(Object.fromEntries(Object.entries(schedule.milestones).map(([key, value]) => [key, localTime(value, schedule.timezone)])));
    setNotes(schedule.notes); setEditing(true); setError("");
  }
  function frozen(key: string) {
    return (["packed", "out"].includes(event.status) && ["prepare_at", "pack_by"].includes(key))
      || ((schedule?.standby || event.status === "out") && key === "standby_at") || (event.status === "out" && key === "dispatch_at");
  }
  async function save(staging?: boolean) {
    if (!schedule || busy) return;
    const body = staging === undefined ? { event_id: event.id, version: schedule.event_version,
      milestones: Object.fromEntries(Object.entries(times).filter(([, value]) => value).map(([key, value]) => [key, frozen(key) ? schedule.milestones[key] : value])), notes }
      : { event_id: event.id, version: schedule.event_version, standby: staging };
    const path = staging === undefined ? "/api/events/logistics" : "/api/events/logistics/stage";
    const payload = JSON.stringify({ path, body });
    if (retry.current.payload !== payload) retry.current = { payload, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      await mutate(path, { ...body, idempotency_key: retry.current.key }, { success: "Logistics recorded." });
      setEditing(false); setAttempt(value => value + 1);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Logistics could not be recorded."); }
    finally { setBusy(false); }
  }
  function submit(form: FormEvent) { form.preventDefault(); void save(); }
  return <section aria-label="Event logistics">
    <div className="section-title-row"><h3>Logistics{schedule?.standby ? " · Standby" : ""}</h3><div className="row-actions"><a className="icon-button" title="Download logistics CSV" aria-label="Download logistics CSV" href={`/api/events/logistics/export?event_id=${encodeURIComponent(event.id)}`}><Download size={17} /></a>{canEdit && !closed ? <button className="button button-secondary" onClick={edit} disabled={busy || !schedule}><Pencil size={16} />Edit logistics</button> : null}</div></div>
    {error ? <p role="alert">{error}</p> : null}
    <p>Show: {event.start_date} {event.start_time} · {event.duration_minutes} minutes</p>
    {editing ? <form onSubmit={submit} className="form-grid"><p>Times: {schedule?.timezone}</p><fieldset className="return-inspection-line" disabled={busy}>
      <div className="form-grid two-columns">{Object.entries(milestones).map(([key, label]) => <label key={key}>{label}<input type="datetime-local" value={times[key] || ""} disabled={Boolean(frozen(key))} onChange={change => setTimes(current => ({ ...current, [key]: change.target.value }))} /></label>)}</div>
      <label>Logistics notes<textarea dir="auto" value={notes} maxLength={4000} onChange={change => setNotes(change.target.value)} /></label>
      <div className="modal-actions"><button className="button button-secondary" type="button" onClick={() => setEditing(false)}>Cancel</button><button className="button button-primary">Save logistics</button></div>
    </fieldset></form> : <><ol className="logistics-timeline">{Object.entries(milestones).map(([key, label]) => <li key={key} aria-current={key === currentStep ? "step" : undefined}><strong>{label}</strong><span>{schedule?.milestones[key] ? localTime(schedule.milestones[key], schedule.timezone).replace("T", " ") : "Not scheduled"}</span>{key === currentStep ? <small>Current warehouse step</small> : null}</li>)}</ol>{schedule?.notes ? <p dir="auto">{schedule.notes}</p> : null}</>}
    {event.status === "packed" && canStage ? <button className="button button-secondary" disabled={busy || !schedule} onClick={() => void save(!schedule?.standby)}>{schedule?.standby ? "Remove from standby" : "Mark standby"}</button> : null}
  </section>;
}
