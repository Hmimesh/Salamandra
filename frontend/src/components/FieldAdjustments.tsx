import { useEffect, useRef, useState, type FormEvent } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { useWorkspace } from "../context/WorkspaceContext";
import { apiRequest } from "../lib/api";
import { inventoryLabel } from "../lib/inventoryLabel";
import { titleCase } from "../lib/format";
import type { EventRecord, EventPlan } from "../types";

type Adjustment = { id: string; version: number; reason: string; status: string; data: { requirements: { capability: string; amount: number }[]; removals: Record<string, number> } };
type Physical = { holding_id: string; label: string; quantity: number; state: string };
type Summary = { event_version: number; adjustments: Adjustment[]; physical: Physical[] };
type Preview = { event_id: string; event_version: number; adjustment_id: string; adjustment_version: number; preview_token: string; plan: EventPlan; waits_for_return: boolean; removals: Record<string, number> };

export function FieldAdjustments({ event }: { event: EventRecord }) {
  const { state, mutate } = useWorkspace();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState("add");
  const [capability, setCapability] = useState("");
  const [holding, setHolding] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const retry = useRef({ payload: "", key: "" });
  const role = state!.auth.user!.role;
  const canRequest = ["owner", "admin", "operator", "producer", "technician"].includes(role);
  const operational = ["packed", "out"].includes(event.status);
  const canFulfill = (event.status === "out" ? ["owner", "admin", "operator"] : ["owner", "admin", "operator", "producer", "technician"]).includes(role);
  useEffect(() => {
    let live = true;
    void apiRequest<Summary>(`/api/events/adjustments?event_id=${encodeURIComponent(event.id)}`)
      .then(result => { if (live) setSummary(result); })
      .catch(failure => { if (live) setError(failure instanceof Error ? failure.message : "Changes are unavailable."); });
    return () => { live = false; };
  }, [event.id, event.version, attempt]);
  async function command(path: string, body: object) {
    if (busy) return;
    const payload = JSON.stringify({ path, body });
    if (retry.current.payload !== payload) retry.current = { payload, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      await mutate(path, { ...body, idempotency_key: retry.current.key }, { success: "Field adjustment recorded." });
      setAttempt(value => value + 1); setEditing(false); setPreview(null); setReason("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Change could not be recorded."); }
    finally { setBusy(false); }
  }
  async function submit(form: FormEvent) {
    form.preventDefault();
    if (!summary) return;
    await command("/api/events/adjustments", { event_id: event.id, version: summary.event_version, reason,
      requirements: mode === "add" ? [{ capability, amount: quantity }] : [],
      removals: mode === "remove" ? [{ holding_id: holding, quantity }] : [] });
  }
  async function review(row: Adjustment) {
    setError(""); setBusy(true);
    try { setPreview(await apiRequest<Preview>("/api/events/adjustments/preview", { method: "POST", body: { event_id: event.id, adjustment_id: row.id } })); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Preview unavailable."); }
    finally { setBusy(false); }
  }
  return <section className="field-adjustments" aria-label="Field adjustments">
    <div className="section-title-row"><h3>Changes</h3><div className="row-actions"><button type="button" className="icon-button" title="Refresh changes" aria-label="Refresh changes" disabled={busy} onClick={() => { setPreview(null); setAttempt(value => value + 1); }}><RefreshCw size={16} /></button>{operational && canRequest ? <button className="button button-secondary" type="button" disabled={busy} onClick={() => setEditing(value => !value)}><Plus size={16} />Request field adjustment</button> : null}</div></div>
    {error ? <p role="alert">{error}</p> : null}
    {editing ? <form className="form-grid" onSubmit={submit}><fieldset disabled={busy} className="return-inspection-line">
      <label>Change<select value={mode} onChange={change => setMode(change.target.value)}><option value="add">Add equipment</option><option value="remove">Reduce equipment</option></select></label>
      {mode === "add" ? <label>Capability<input value={capability} required maxLength={128} onChange={change => setCapability(change.target.value)} list="adjustment-capabilities" /><datalist id="adjustment-capabilities">{[...new Set(state!.inventory.items.flatMap(item => item.capabilities))].sort().map(value => <option key={value} value={value} />)}</datalist></label> : <label>Equipment<select required value={holding} onChange={change => setHolding(change.target.value)}><option value="">Choose equipment</option>{summary?.physical.map(line => <option key={line.holding_id} value={line.holding_id}>{line.label} ({line.quantity})</option>)}</select></label>}
      <label>Quantity<input type="number" min="1" max="10000" step="1" required value={quantity} onChange={change => setQuantity(Number(change.target.value))} /></label>
      <label>Reason<textarea dir="auto" required maxLength={1000} value={reason} onChange={change => setReason(change.target.value)} /></label>
      <div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setEditing(false)}>Cancel</button><button className="button button-primary" disabled={!summary}>Record request</button></div>
    </fieldset></form> : null}
    {summary?.adjustments.map(row => <article className="maintenance-row" key={row.id}><div><strong dir="auto">{row.reason}</strong><p>{titleCase(row.status)}</p>{row.data.requirements.map((req, index) => <p key={index}>+{req.amount} {titleCase(req.capability)}</p>)}{Object.entries(row.data.removals).map(([id, amount]) => <p key={id}>-{amount} {summary.physical.find(line => line.holding_id === id)?.label || "Event equipment"}</p>)}</div>{row.status === "pending" && operational && canRequest ? <div className="row-actions"><button className="button button-secondary" disabled={busy} onClick={() => void review(row)}>Review fulfillment</button><button className="button button-secondary" disabled={busy} onClick={() => void command("/api/events/adjustments/cancel", { event_id: event.id, adjustment_id: row.id, version: summary.event_version, adjustment_version: row.version })}>Cancel request</button></div> : null}</article>)}
    {preview ? <section aria-label="Fulfillment preview"><h4>Fulfillment preview</h4>{preview.plan.lines.map((line, index) => <p key={index}>{line.amount} × {inventoryLabel(state!.inventory.items.find(item => item.id === line.item_id))}{line.missing ? ` · ${line.missing} missing` : ""}</p>)}{Object.entries(preview.removals).map(([id, amount]) => <p key={id}>Release {amount} × {summary?.physical.find(line => line.holding_id === id)?.label || "Event equipment"}</p>)}{preview.waits_for_return ? <p>Equipment remains out until inspected return.</p> : <button type="button" className="button button-primary" disabled={busy || !canFulfill || Boolean(preview.plan.total_missing)} onClick={() => void command("/api/events/adjustments/fulfill", { event_id: event.id, adjustment_id: preview.adjustment_id, version: preview.event_version, adjustment_version: preview.adjustment_version, preview_token: preview.preview_token })}>{event.status === "out" ? "Confirm supplemental dispatch" : "Confirm physical packing / release"}</button>}</section> : null}
  </section>;
}
