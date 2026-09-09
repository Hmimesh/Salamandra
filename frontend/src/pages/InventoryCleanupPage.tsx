import { Link } from "react-router-dom";
import { useEffect, useState, type FormEvent } from "react";
import { Check, Pencil, Plus, RefreshCw, X } from "lucide-react";
import { Modal, PageHeader } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";
import { apiRequest } from "../lib/api";
import { titleCase } from "../lib/format";
import { normalizeSearch } from "../lib/catalog";

type Item = { target: string; item_id: string; name: string; missing: string[] };
type Pair = { left: Item; right: Item; pair_key: string; evidence: string };
type Cleanup = { counts: Record<string, number>; pairs: Pair[]; issues: Item[]; reviewed_pairs: number };
type Catalog = { canonical_types: string[]; terms: { kind: string; canonical_code: string; label: string }[] };

export function InventoryCleanupPage() {
  const { state, mutate, notify } = useWorkspace();
  const allowed = ["owner", "admin"].includes(state!.auth.user!.role);
  const [data, setData] = useState<Cleanup | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [aliasOpen, setAliasOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function reload() {
    try { setData(await apiRequest<Cleanup>("/api/inventory/cleanup")); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Cleanup could not be loaded."); }
  }
  useEffect(() => {
    if (!allowed) return;
    let active = true;
    void Promise.all([apiRequest<Cleanup>("/api/inventory/cleanup"), apiRequest<Catalog>("/api/catalog")]).then(([result, terms]) => { if (active) { setData(result); setCatalog(terms); } }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [allowed]);
  async function decide(pair: Pair, action: string) {
    setBusy(true);
    try { await mutate("/api/inventory/cleanup/decision", { left: pair.left.target, right: pair.right.target, pair_key: pair.pair_key, action }); await reload(); }
    catch { /* API error is reported by the workspace provider. */ }
    finally { setBusy(false); }
  }
  async function addAlias(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      await mutate("/api/catalog/aliases", { label: form.get("label"), canonical_type: form.get("canonical_type"), idempotency_key: crypto.randomUUID() });
      setAliasOpen(false); notify("Workspace alias saved."); await reload();
    } catch { /* The dialog remains available for correction. */ }
    finally { setBusy(false); }
  }
  if (!allowed) return <div className="page"><PageHeader title="Inventory cleanup" description="Workspace administrator access is required." /></div>;
  const issues = (data?.issues || []).filter(item => normalizeSearch(item.name).includes(normalizeSearch(search)));
  return <div className="page inventory-cleanup-page">
    <PageHeader title="Inventory cleanup" description="Shared inventory · optional product details and duplicate review" />
    <div className="import-toolbar"><Link to="/inventory" className="button button-secondary">Inventory</Link><button className="button button-secondary" onClick={() => void reload()} disabled={busy}><RefreshCw size={16} />Refresh</button><button className="button button-primary" onClick={() => setAliasOpen(true)} disabled={!catalog}><Plus size={16} />Add category alias</button></div>
    {error ? <p role="alert" className="import-error">{error}</p> : null}
    {!data && !error ? <p role="status">Loading inventory review...</p> : null}
    {data ? <>
      <dl className="import-totals">{Object.entries(data.counts).map(([key, value]) => <div key={key}><dt>{titleCase(key)}</dt><dd>{value}</dd></div>)}</dl>
      <section className="cleanup-section"><h2>Possible duplicates</h2><p>{data.reviewed_pairs} pairs reviewed. Product decisions never combine stock or rewrite event history.</p>
        {data.pairs.length ? data.pairs.map(pair => <div className="import-review-row" key={pair.pair_key}><strong dir="auto">{pair.left.name}</strong><strong dir="auto">{pair.right.name}</strong><p>{pair.evidence}</p><div className="import-toolbar"><button className="button button-secondary" disabled={busy} onClick={() => void decide(pair, "same")}><Check size={16} />Same intended product</button><button className="button button-secondary" disabled={busy} onClick={() => void decide(pair, "separate")}><X size={16} />Keep separate</button></div></div>) : <p>No unreviewed duplicate candidates.</p>}
        {data.counts.duplicates > data.pairs.length ? <p>Showing the first 200 pairs. Review these to load more.</p> : null}
      </section>
      <section className="cleanup-section"><h2>Product details</h2><p>Manufacturer, model and location are optional. Generic equipment can stay unnamed by brand.</p><label>Search product details<input type="search" dir="auto" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} /></label>
        <div className="import-table" tabIndex={0}><table><thead><tr><th>Item</th><th>Missing details</th><th>Edit</th></tr></thead><tbody>{issues.slice(page * 25, (page + 1) * 25).map(item => <tr key={item.target}><td dir="auto">{item.name}</td><td>{item.missing.map(titleCase).join(", ")}</td><td><Link className="icon-button" aria-label={`Edit ${item.name}`} title="Edit product details" to={`/inventory?edit=${encodeURIComponent(item.item_id)}`}><Pencil size={16} /></Link></td></tr>)}</tbody></table></div>
        <div className="import-pagination"><button className="button button-secondary" disabled={!page} onClick={() => setPage(page - 1)}>Previous</button><span>{page + 1} / {Math.max(1, Math.ceil(issues.length / 25))}</span><button className="button button-secondary" disabled={(page + 1) * 25 >= issues.length} onClick={() => setPage(page + 1)}>Next</button></div>
      </section>
    </> : null}
    <Modal open={aliasOpen} title="Add category alias" onClose={() => { if (!busy) setAliasOpen(false); }}><form className="form-stack" onSubmit={addAlias}><label>Imported category label<input name="label" dir="auto" required maxLength={200} /></label><label>Canonical category<select name="canonical_type" required>{catalog?.canonical_types.map(code => <option key={code} value={code}>{titleCase(code)}</option>)}{catalog?.terms.filter(term => term.kind === "category").map(term => <option key={term.canonical_code} value={term.canonical_code}>{term.label}</option>)}</select></label><div className="modal-actions"><button type="button" className="button button-secondary" disabled={busy} onClick={() => setAliasOpen(false)}>Cancel</button><button className="button button-primary" disabled={busy}>Save alias</button></div></form></Modal>
  </div>;
}
