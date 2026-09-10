import { useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { Modal } from "./ui";
import { apiRequest } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import type { EventFeedback, EventLearning, EventRecord } from "../types";

type Props = { event: EventRecord | null; onClose: () => void };
const questions = [
  ["missing", "Was anything missing?"],
  ["unnecessary", "Was anything unnecessary?"],
  ["failed", "Did anything fail?"],
  ["additional_onsite", "Was anything added onsite?"],
] as const;

export function EventReviewModal({ event, onClose }: Props) {
  const { mutate } = useWorkspace();
  const [learning, setLearning] = useState<EventLearning | null>(null);
  const [form, setForm] = useState({ missing: "no", unnecessary: "no", failed: "no", additional_onsite: "no", plan_fit: "about_right", reuse_plan: "yes", notes: "" });
  const [saving, setSaving] = useState(false);
  const [affected, setAffected] = useState<Record<string, { item_id: string; quantity: string; note: string }>>({});
  useEffect(() => {
    if (!event) return;
    void apiRequest<{ learning: EventLearning }>(`/api/events/learning?event_id=${encodeURIComponent(event.id)}`).then((result) => {
      setLearning(result.learning);
      const feedback = result.learning.feedback;
      if (feedback) setForm({ missing: feedback.missing, unnecessary: feedback.unnecessary, failed: feedback.failed, additional_onsite: feedback.additional_onsite, plan_fit: feedback.plan_fit, reuse_plan: feedback.reuse_plan, notes: feedback.notes });
    }).catch(() => undefined);
  }, [event]);
  if (!event) return null;
  const currentEvent = event;
  const lines = currentEvent.plan.lines.filter((line) => line.item_id);
  async function save() {
    setSaving(true);
    try {
      const items = Object.entries(affected).filter(([, value]) => value.item_id).map(([kind, value]) => ({ kind, item_id: value.item_id, quantity: value.quantity ? Number(value.quantity) : null, note: value.note }));
      await mutate("/api/events/feedback", { event_id: currentEvent.id, event_version: currentEvent.version, feedback_version: learning?.feedback?.version, idempotency_key: crypto.randomUUID(), ...form, items }, { success: "Event review saved." });
      onClose();
    } finally { setSaving(false); }
  }
  return <Modal open title="Review this event" description="A quick operational note for the next time. This never changes the return record." onClose={onClose}>
    <form className="form-stack event-review-form" onSubmit={(submit) => { submit.preventDefault(); void save(); }}>
      {questions.map(([key, label]) => <fieldset key={key}><legend>{label}</legend><label><input type="radio" name={key} checked={form[key] === "no"} onChange={() => setForm({ ...form, [key]: "no" })} /> No</label><label><input type="radio" name={key} checked={form[key] === "yes"} onChange={() => setForm({ ...form, [key]: "yes" })} /> Yes</label>{form[key] === "yes" ? <div className="event-review-affected"><label>Item<select aria-label={`${label} item`} value={affected[key]?.item_id || ""} onChange={(change) => setAffected({ ...affected, [key]: { item_id: change.target.value, quantity: affected[key]?.quantity || "1", note: affected[key]?.note || "" } })}><option value="">Choose an item</option>{lines.map((line) => <option key={line.item_id} value={line.item_id}>{line.item_id}</option>)}</select></label><label>Qty<input type="number" min="1" value={affected[key]?.quantity || "1"} onChange={(change) => setAffected({ ...affected, [key]: { item_id: affected[key]?.item_id || "", quantity: change.target.value, note: affected[key]?.note || "" } })} /></label><label>Note<input dir="auto" value={affected[key]?.note || ""} onChange={(change) => setAffected({ ...affected, [key]: { item_id: affected[key]?.item_id || "", quantity: affected[key]?.quantity || "1", note: change.target.value } })} /></label></div> : null}</fieldset>)}
      <label>How was the equipment plan?<select value={form.plan_fit} onChange={(change) => setForm({ ...form, plan_fit: change.target.value })}><option value="too_little">Too little</option><option value="about_right">About right</option><option value="too_much">Too much</option></select></label>
      <label>Would you use this plan again?<select value={form.reuse_plan} onChange={(change) => setForm({ ...form, reuse_plan: change.target.value })}><option value="yes">Yes</option><option value="with_changes">With changes</option><option value="no">No</option></select></label>
      <label>Notes <textarea dir="auto" maxLength={2000} rows={4} value={form.notes} onChange={(change) => setForm({ ...form, notes: change.target.value })} placeholder="Anything the next crew should know?" /></label>
      {learning?.feedback ? <p className="form-help"><CheckCircle2 size={15} /> Editing the saved review keeps its version and audit history.</p> : null}
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Skip for now</button><button type="submit" className="button button-primary" disabled={saving}>{saving ? "Saving..." : "Save review"}</button></div>
    </form>
  </Modal>;
}
