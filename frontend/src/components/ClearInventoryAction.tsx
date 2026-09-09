import { Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { useWorkspace } from "../context/WorkspaceContext";
import { apiRequest } from "../lib/api";
import { Modal } from "./ui";

type Summary = { definitions: number; units: number; preview_token: string; blocked: string };

export function ClearInventoryAction() {
  const { state, mutate, refresh, notify } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const key = useRef(crypto.randomUUID());

  async function review() {
    setOpen(true); setBusy(true); setSummary(null); setError(""); setConfirmation("");
    key.current = crypto.randomUUID();
    try { setSummary(await apiRequest<Summary>("/api/inventory/clear.preview")); }
    catch (e) { setError(e instanceof Error ? e.message : "Inventory summary could not be loaded."); }
    finally { setBusy(false); }
  }
  async function clear() {
    if (!summary || busy) return;
    setBusy(true); setError("");
    try {
      const result = await mutate("/api/inventory/clear", { confirmation, preview_token: summary.preview_token, idempotency_key: key.current });
      if (typeof result.batch_id !== "string" || !Number.isInteger(result.archived)) throw new Error("Confirmation could not be read. Retry without changing this request.");
      await refresh(); setOpen(false); notify("Shared inventory cleared. History preserved.");
    } catch (e) { setError(e instanceof Error ? e.message : "Inventory could not be cleared."); }
    finally { setBusy(false); }
  }
  if (state?.auth.user?.role !== "owner") return null;
  return <section className="cleanup-section">
    <h2>Workspace inventory</h2>
    <button className="button button-secondary danger-icon" onClick={() => void review()}><Trash2 size={17} />Clear active inventory</button>
    <Modal open={open} title="Clear active inventory?" onClose={() => { if (!busy) setOpen(false); }}>
      <p>Archive all active shared inventory in <bdi>{state.organization.name}</bdi>. Personal inventory, events, kits, catalog mappings and history stay unchanged.</p>
      {!summary && busy ? <p role="status">Checking inventory...</p> : null}
      {summary ? <dl className="import-totals"><div><dt>Definitions to archive</dt><dd>{summary.definitions}</dd></div><div><dt>Available units to remove</dt><dd>{summary.units}</dd></div></dl> : null}
      {summary?.blocked ? <p role="alert" className="import-error">{summary.blocked}</p> : null}
      {error ? <p role="alert" className="import-error">{error}</p> : null}
      <label className="form-stack">Type CLEAR INVENTORY<input value={confirmation} onChange={e => setConfirmation(e.target.value)} autoComplete="off" disabled={busy} /></label>
      <div className="modal-actions"><button className="button button-secondary" disabled={busy} onClick={() => setOpen(false)}>Cancel</button><button className="button button-danger" disabled={busy || !summary?.definitions || Boolean(summary.blocked) || confirmation !== "CLEAR INVENTORY"} onClick={() => void clear()}>Clear shared inventory</button></div>
    </Modal>
  </section>;
}
