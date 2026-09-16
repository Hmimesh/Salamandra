import { useEffect, useRef, useState, type FormEvent } from "react";
import { Modal } from "./ui";
import { apiRequest } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import type { EventRecord } from "../types";

type Line = { holding_id: string; label: string; quantity: number; ready: number; damaged: number; missing: number; reason: string };

export function ReturnInspection({ event, onClose }: { event: EventRecord | null; onClose: () => void }) {
  const { mutate } = useWorkspace();
  const [lines, setLines] = useState<Line[]>([]);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef({ payload: "", key: "" });
  useEffect(() => {
    let live = true;
    if (!event) return;
    setLoading(true); setError(""); setVersion(0); setLines([]);
    request.current = { payload: "", key: "" };
    void apiRequest<{ version: number; lines: Pick<Line, "holding_id" | "label" | "quantity">[] }>(`/api/events/return?event_id=${encodeURIComponent(event.id)}`)
      .then(result => { if (live) { setVersion(result.version); setLines(result.lines.map(line => ({ ...line, ready: line.quantity, damaged: 0, missing: 0, reason: "" }))); } })
      .catch(failure => { if (live) setError(failure instanceof Error ? failure.message : "Return details are unavailable."); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [event?.id]);
  function update(index: number, change: Partial<Line>) {
    setLines(current => current.map((line, at) => at === index ? { ...line, ...change } : line));
  }
  const valid = version > 0 && lines.every(line => [line.ready, line.damaged, line.missing].every(value => Number.isInteger(value) && value >= 0)
    && line.ready + line.damaged + line.missing === line.quantity && (!(line.damaged || line.missing) || line.reason.trim()));
  async function submit(form: FormEvent) {
    form.preventDefault();
    if (!event || !valid || busy) return;
    const body = { event_id: event.id, version, lines: lines.map(({ holding_id, ready, damaged, missing, reason }) => ({ holding_id, ready, damaged, missing, reason })) };
    const payload = JSON.stringify(body);
    if (request.current.payload !== payload) request.current = { payload, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      await mutate("/api/events/return", { ...body, idempotency_key: request.current.key }, { success: "Return reconciled. Only ready equipment is available again." });
      onClose();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Return could not be recorded."); }
    finally { setBusy(false); }
  }
  return <Modal open={Boolean(event)} title="Inspect event return" onClose={() => { if (!busy) onClose(); }}>
    <form onSubmit={submit} className="form-grid">
      <p dir="auto">{event?.title}</p>
      {loading ? <p role="status">Loading dispatched equipment...</p> : lines.map((line, index) => <fieldset className="return-inspection-line" key={line.holding_id} disabled={busy}>
        <legend dir="auto">{line.label} · {line.quantity} out</legend>
        <div className="return-inspection-quantities">{(["ready", "damaged", "missing"] as const).map(key => <label key={key}>{key === "ready" ? "Ready" : key === "damaged" ? "Damaged" : "Missing"}<input type="number" min="0" max={line.quantity} step="1" required value={line[key]} onChange={change => update(index, { [key]: Number(change.target.value) })} /></label>)}</div>
        {line.ready + line.damaged + line.missing !== line.quantity ? <p role="status">Account for exactly {line.quantity} units.</p> : null}
        {line.damaged || line.missing ? <label>Issue / missing details<textarea dir="auto" maxLength={1000} required value={line.reason} onChange={change => update(index, { reason: change.target.value })} /></label> : null}
      </fieldset>)}
      {error ? <p role="alert">{error}</p> : null}
      <div className="modal-actions"><button className="button button-secondary" type="button" onClick={onClose} disabled={busy}>Cancel</button><button className="button button-primary" disabled={busy || loading || !valid}>{busy ? "Recording..." : "Complete inspected return"}</button></div>
    </form>
  </Modal>;
}
