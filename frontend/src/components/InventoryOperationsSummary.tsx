import { useEffect, useState } from "react";
import { apiRequest } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";

const labels: Record<string, string> = { ready: "Ready", reserved: "Reserved", packed: "Packed", standby: "Standby", out: "Out", needs_repair: "Needs repair", in_repair: "In repair", quarantine: "Quarantine", missing: "Missing", retired: "Retired" };
export function InventoryOperationsSummary({ scope }: { scope: string }) {
  const { state } = useWorkspace();
  const [totals, setTotals] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { let live = true; setTotals(null); setError("");
    void apiRequest<Record<string, number>>(`/api/inventory/operations?scope=${scope}`).then(result => { if (live) setTotals(result); }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [scope, state]);
  return <section aria-label="Inventory operational quantities">{error ? <p role="alert">{error}</p> : <dl className="inventory-operational-totals">{Object.entries(labels).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{totals ? totals[key] : "…"}</dd></div>)}</dl>}</section>;
}
