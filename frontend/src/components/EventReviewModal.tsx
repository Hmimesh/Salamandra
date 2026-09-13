import { useEffect, useRef, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { Modal } from "./ui";
import { apiRequest } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import { inventoryLabel } from "../lib/inventoryLabel";
import type { EventFeedbackItem, EventLearning, EventRecord } from "../types";

type Props = { event: EventRecord | null; onClose: () => void };
const questions = [
  ["missing", "Was anything missing?"],
  ["unnecessary", "Was anything unnecessary?"],
  ["failed", "Did anything fail?"],
  ["additional_onsite", "Was anything added onsite?"],
] as const;
const defaults = { missing: "no", unnecessary: "no", failed: "no", additional_onsite: "no", plan_fit: "about_right", reuse_plan: "yes", notes: "" };
type AffectedRow = Omit<EventFeedbackItem, "quantity"> & { key: string; quantity: string; saved: boolean };

export function EventReviewModal({ event, onClose }: Props) {
  // Each opened event owns a fresh session, including A -> B -> A races.
  return event ? <ReviewSession key={event.id} event={event} onClose={onClose} /> : null;
}

function ReviewSession({ event, onClose }: { event: EventRecord; onClose: () => void }) {
  const { state, mutate } = useWorkspace();
  const [learning, setLearning] = useState<EventLearning | null>(null);
  const [form, setForm] = useState(defaults);
  const [affected, setAffected] = useState<AffectedRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const current = useRef(false);
  useEffect(() => {
    let active = true;
    current.current = true;
    setLoading(true);
    setError("");
    setLearning(null);
    setForm(defaults);
    setAffected([]);
    void apiRequest<{ learning: EventLearning }>(`/api/events/learning?event_id=${encodeURIComponent(event.id)}`).then(result => {
      if (!active) return;
      setLearning(result.learning);
      const feedback = result.learning.feedback;
      if (feedback) {
        setForm({ missing: feedback.missing, unnecessary: feedback.unnecessary, failed: feedback.failed, additional_onsite: feedback.additional_onsite, plan_fit: feedback.plan_fit, reuse_plan: feedback.reuse_plan, notes: feedback.notes });
        const rows = feedback.items.map(item => ({ ...item, saved: true, key: crypto.randomUUID(), quantity: item.quantity === null ? "" : String(item.quantity) }));
        for (const [kind] of questions) {
          if (feedback[kind] === "yes" && !rows.some(row => row.kind === kind)) rows.push({ key: crypto.randomUUID(), saved: false, kind, item_id: null, label_snapshot: "", quantity: "1", note: "" });
        }
        setAffected(rows);
      }
      setLoading(false);
    }).catch(() => {
      if (active) { setError("Could not load this event review. Retry before saving."); setLoading(false); }
    });
    return () => { active = false; current.current = false; };
  }, [event.id, attempt]);

  const lines = event.plan.lines.filter(line => line.item_id);
  function answer(kind: EventFeedbackItem["kind"], value: string) {
    setForm({ ...form, [kind]: value });
    if (value === "no") setAffected(affected.filter(row => row.kind !== kind));
    else if (!affected.some(row => row.kind === kind)) setAffected([...affected, { key: crypto.randomUUID(), saved: false, kind, item_id: null, label_snapshot: "", quantity: "1", note: "" }]);
  }
  function update(key: string, values: Partial<AffectedRow>) {
    setAffected(rows => rows.map(row => row.key === key ? { ...row, ...values } : row));
  }
  async function save() {
    if (loading || error || saving || !learning || !current.current) return;
    setSaving(true);
    try {
      const items = affected.filter(row => row.saved || row.item_id || row.label_snapshot).map(({ key: _key, saved: _saved, quantity, ...item }) => ({ ...item, quantity: quantity === "" ? null : Number(quantity) }));
      await mutate("/api/events/feedback", { event_id: event.id, event_version: event.version, feedback_version: learning.feedback?.version, idempotency_key: crypto.randomUUID(), ...form, items }, { success: "Event review saved." });
      if (current.current) onClose();
    } catch {
      // The workspace provider displays the authoritative API conflict/error.
    } finally { if (current.current) setSaving(false); }
  }
  return <Modal open title="Review this event" description={event.title} onClose={onClose}>
    <form className="form-stack event-review-form" onSubmit={submit => { submit.preventDefault(); void save(); }}>
      {loading ? <p role="status">Loading event review...</p> : error ? <div role="alert"><p>{error}</p><button type="button" className="button button-secondary" onClick={() => setAttempt(value => value + 1)}>Retry</button></div> : <>
        {questions.map(([kind, label]) => <fieldset key={kind}><legend>{label}</legend>
          <label><input type="radio" name={kind} checked={form[kind] === "no"} onChange={() => answer(kind, "no")} /> No</label>
          <label><input type="radio" name={kind} checked={form[kind] === "yes"} onChange={() => answer(kind, "yes")} /> Yes</label>
          {form[kind] === "yes" ? affected.filter(row => row.kind === kind).map(row => <div className="event-review-affected" key={row.key}>
            <label>Item{!row.item_id && row.label_snapshot ? <input dir="auto" readOnly value={row.label_snapshot} /> : <select aria-label={`${label} item`} value={row.item_id || ""} onChange={change => update(row.key, { item_id: change.target.value || null })}>
              <option value="">Choose an item</option>{lines.map(line => <option key={line.item_id} value={line.item_id}>{inventoryLabel(state!.inventory.items.find(item => item.id === line.item_id))}</option>)}
            </select>}</label>
            <label>Qty<input type="number" min="1" value={row.quantity} onChange={change => update(row.key, { quantity: change.target.value })} /></label>
            <label>Note<input dir="auto" maxLength={500} value={row.note} onChange={change => update(row.key, { note: change.target.value })} /></label>
          </div>) : null}
        </fieldset>)}
        <label>How was the equipment plan?<select value={form.plan_fit} onChange={change => setForm({ ...form, plan_fit: change.target.value })}><option value="too_little">Too little</option><option value="about_right">About right</option><option value="too_much">Too much</option></select></label>
        <label>Would you use this plan again?<select value={form.reuse_plan} onChange={change => setForm({ ...form, reuse_plan: change.target.value })}><option value="yes">Yes</option><option value="with_changes">With changes</option><option value="no">No</option></select></label>
        <label>Notes <textarea aria-label="Notes" dir="auto" maxLength={2000} rows={4} value={form.notes} onChange={change => setForm({ ...form, notes: change.target.value })} placeholder="Anything the next crew should know?" /></label>
        {learning?.feedback ? <p className="form-help"><CheckCircle2 size={15} /> Editing the saved review keeps its version and audit history.</p> : null}
      </>}
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Skip for now</button><button type="submit" className="button button-primary" disabled={loading || Boolean(error) || saving || !learning}>{saving ? "Saving..." : "Save review"}</button></div>
    </form>
  </Modal>;
}
