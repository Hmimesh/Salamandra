import { type FormEvent, useEffect, useRef, useState } from "react";
import { Plus, RefreshCw, Wrench } from "lucide-react";
import { PageHeader, Modal, EmptyState } from "../components/ui";
import { InventoryDependencyPicker } from "../components/InventoryDependencyPicker";
import { useWorkspace } from "../context/WorkspaceContext";
import { apiRequest } from "../lib/api";
import { titleCase } from "../lib/format";

type Incident = { id: string; label: string; quantity: number; status: string; issue: string; resolution: string; version: number; reported_at: string; allowed_transitions: string[] };
const states = ["needs_repair", "in_repair", "quarantine", "missing", "ready", "retired"];

export function MaintenancePage() {
  const { state, mutate } = useWorkspace();
  const [rows, setRows] = useState<Incident[]>([]);
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reporting, setReporting] = useState(false);
  const [selected, setSelected] = useState<Incident | null>(null);
  const [scope, setScope] = useState<"shared" | "personal">("shared");
  const [item, setItem] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [status, setStatus] = useState("needs_repair");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef({ payload: "", key: "" });
  useEffect(() => {
    let live = true;
    setLoading(true); setError("");
    void apiRequest<{ incidents: Incident[]; quantities: Record<string, number> }>(`/api/maintenance${filter ? `?status=${encodeURIComponent(filter)}` : ""}`)
      .then(result => { if (live) { setRows(result.incidents); setQuantities(result.quantities); } })
      .catch(failure => { if (live) setError(failure instanceof Error ? failure.message : "Maintenance is unavailable."); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [filter, attempt]);
  function close() { if (!busy) { setReporting(false); setSelected(null); setError(""); } }
  function open(row: Incident | null) {
    request.current = { payload: "", key: "" };
    setSelected(row); setReporting(!row); setNote(""); setItem(""); setQuantity(1);
    setStatus(row?.allowed_transitions[0] || "needs_repair"); setError("");
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!selected && !item) { setError("Choose an inventory item."); return; }
    const body = selected ? { incident_id: selected.id, version: selected.version, status, reason: note }
      : { scope, item_id: item, quantity, status, issue: note };
    const payload = JSON.stringify(body);
    if (request.current.payload !== payload) request.current = { payload, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      await mutate(`/api/maintenance/${selected ? "transition" : "report"}`, { ...body, idempotency_key: request.current.key }, { success: "Equipment condition recorded." });
      setSelected(null); setReporting(false); setAttempt(value => value + 1);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The condition could not be recorded."); }
    finally { setBusy(false); }
  }
  return <div className="page">
    <PageHeader title="Maintenance" description="Equipment condition and repair history." actions={<button className="button button-primary" onClick={() => open(null)}><Plus size={16} />Report issue</button>} />
    {!loading && !error ? <dl className="condition-summary" aria-label="Equipment condition quantities">{states.filter(value => value !== "ready").map(value => <div key={value}><dt>{titleCase(value)}</dt><dd>{quantities[value] ?? 0}</dd></div>)}</dl> : null}
    <section className="data-section">
      <div className="section-title-row"><label>Condition<select value={filter} onChange={event => setFilter(event.target.value)}><option value="">Open incidents</option>{states.map(value => <option key={value} value={value}>{titleCase(value)}</option>)}</select></label><button className="icon-button" aria-label="Refresh maintenance" title="Refresh maintenance" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={18} /></button></div>
      {error && !reporting && !selected ? <p role="alert">{error}</p> : null}
      {loading ? <p role="status">Loading maintenance...</p> : rows.length ? <div className="maintenance-list">{rows.map(row => <article key={row.id} className="maintenance-row"><div><h2 dir="auto">{row.quantity} × {row.label}</h2><p dir="auto">{row.issue}</p><small>{titleCase(row.status)} · {new Date(row.reported_at).toLocaleDateString()}</small>{row.resolution ? <p dir="auto">{row.resolution}</p> : null}</div>{row.allowed_transitions.length ? <button className="button button-secondary" onClick={() => open(row)}><Wrench size={16} />Update condition</button> : null}</article>)}</div> : <EmptyState title="No matching incidents" message="No equipment incidents match this condition." />}
    </section>
    <Modal open={reporting || Boolean(selected)} title={selected ? "Update condition" : "Report equipment issue"} onClose={close}>
      <form onSubmit={submit} className="form-grid">
        {selected ? <p>{selected.quantity} × {selected.label}</p> : <><label>Inventory source<select value={scope} onChange={event => { setScope(event.target.value as "shared" | "personal"); setItem(""); }}><option value="shared">Shared inventory</option><option value="personal">My inventory</option></select></label><InventoryDependencyPicker label="Equipment" items={state!.inventories[scope].items.filter(candidate => candidate.count > 0)} value={item} onChange={setItem} /><label>Quantity<input type="number" min="1" max="10000" step="1" value={quantity} onChange={event => setQuantity(Number(event.target.value))} required /></label></>}
        <label>New condition<select value={status} onChange={event => setStatus(event.target.value)}>{(selected ? selected.allowed_transitions : ["needs_repair", "quarantine", "missing", "retired"]).map(value => <option key={value} value={value}>{titleCase(value)}</option>)}</select></label>
        <label>Reason<textarea dir="auto" value={note} onChange={event => setNote(event.target.value)} maxLength={1000} required /></label>
        {error ? <p role="alert">{error}</p> : null}
        <div className="modal-actions"><button type="button" className="button button-secondary" disabled={busy} onClick={close}>Cancel</button><button className="button button-primary" type="submit" disabled={busy}>{busy ? "Recording..." : "Record condition"}</button></div>
      </form>
    </Modal>
  </div>;
}
