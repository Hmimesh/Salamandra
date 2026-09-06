import { Boxes, CheckCircle2, PackageCheck, Play, Plus, ShieldAlert, Sparkles, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { EmptyState, Modal, PageHeader } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";
import { titleCase } from "../lib/format";
import type { EventPlan, EventRecord, EventTemplate, StateEnvelope } from "../types";

type SelectedPlan = {
  source: EventTemplate;
  type: "kit" | "template";
  plan: EventPlan;
  checkoutKey: string;
};

export function KitsPage() {
  const { state, mutate } = useWorkspace();
  const [tab, setTab] = useState<"kits" | "templates">("kits");
  const [selected, setSelected] = useState<SelectedPlan | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [createKey, setCreateKey] = useState(() => crypto.randomUUID());
  const [newKit, setNewKit] = useState({ name: "", description: "", notes: "", items: [{ id: crypto.randomUUID(), item_id: "", amount: 1 }] });
  const sources = tab === "kits" ? state!.templates.kits : state!.templates.templates;
  const suggestions = tab === "kits" ? state!.templates.suggested_kits : state!.templates.suggested_templates;

  async function preview(source: EventTemplate, type: "kit" | "template") {
    try {
      const response = await mutate<StateEnvelope & { plan: EventPlan }>("/api/events/template", { source_type: type, source_id: source.id });
      setSelected({ source, type, plan: response.plan, checkoutKey: crypto.randomUUID() });
    } catch {
      // The workspace provider reports the API message.
    }
  }

  async function reserve() {
    if (!selected) return;
    try {
      const response = await mutate<StateEnvelope & { event: EventRecord; plan: EventPlan }>("/api/kits/checkout", { source_id: selected.source.id, idempotency_key: selected.checkoutKey }, { success: `${selected.source.name} checked out from inventory.` });
      setSelected({ ...selected, plan: response.plan });
    } catch {
      // The workspace provider reports the API message.
    }
  }

  function startFromSuggestion(source?: EventTemplate) {
    setCreateKey(crypto.randomUUID());
    setNewKit({
      name: source?.name || "",
      description: source?.description || "",
      notes: "",
      items: source?.items.map((item) => ({ id: crypto.randomUUID(), item_id: item.item_id, amount: item.amount })) || [{ id: crypto.randomUUID(), item_id: "", amount: 1 }],
    });
    setCreating(true);
  }

  async function createKit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await mutate("/api/kits/create", { ...newKit, idempotency_key: createKey, items: newKit.items.map(({ item_id, amount }) => ({ item_id, amount })) }, { success: "Kit saved to the workspace." });
      setCreating(false);
      setNewKit({ name: "", description: "", notes: "", items: [{ id: crypto.randomUUID(), item_id: "", amount: 1 }] });
      setCreateKey(crypto.randomUUID());
    } catch {
      // Keep the form and retry key; the workspace provider reports the API error.
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page kits-page">
      <PageHeader title="Kits & templates" description="Keep reusable equipment packages and event starters for your workspace." actions={<button className="button button-primary" type="button" onClick={() => startFromSuggestion()}><Plus size={17} />Create kit</button>} />
      <div className="toolbar-row"><div className="segmented-control"><button className={tab === "kits" ? "active" : ""} onClick={() => setTab("kits")}>Equipment kits</button><button className={tab === "templates" ? "active" : ""} onClick={() => setTab("templates")}>Event templates</button></div><span className="result-count">{sources.length} in workspace</span></div>
      {sources.length ? <section className="library-list">
        {sources.map((source) => {
          const readyLines = source.items.filter((need) => need.amount <= (state!.inventory.items.find((item) => item.id === need.item_id)?.count || 0)).length;
          const fullyReady = readyLines === source.items.length;
          return <article className="library-row" key={source.id}><span className={`library-icon ${fullyReady ? "ready" : "warning"}`}>{tab === "kits" ? <Boxes size={22} /> : <Sparkles size={22} />}</span><div className="library-copy"><span>{source.category ? titleCase(source.category) : "Event template"}</span><h2>{source.name}</h2><p>{source.description}</p><div className="library-items">{source.items.map((item) => <span key={item.item_id}>{item.amount}x {titleCase(item.item_id)}</span>)}</div></div><div className="library-status">{fullyReady ? <span className="available"><CheckCircle2 size={16} />Ready from stock</span> : <span className="limited"><ShieldAlert size={16} />Check availability</span>}<small>{source.items.length} equipment lines</small></div><button className="button button-secondary" onClick={() => void preview(source, tab === "kits" ? "kit" : "template")}><Play size={16} />Check plan</button></article>;
        })}
      </section> : <section className="data-section"><EmptyState title={tab === "kits" ? "No kits yet" : "No event templates yet"} message={tab === "kits" ? "This workspace starts empty. Use a suggested starter only when it fits your operation." : "This workspace starts empty. Preview a suggested event starter before deciding to use it."} /></section>}

      <section className="suggested-library" aria-labelledby="suggested-library-title">
        <div className="section-title-row"><div><h2 id="suggested-library-title">Suggested starters</h2><p>Preview only. Suggestions are not added to your workspace automatically.</p></div><span className="result-count">{suggestions.length} suggestions</span></div>
        <div className="library-list">
          {suggestions.map((source) => {
            const readyLines = source.items.filter((need) => need.amount <= (state!.inventory.items.find((item) => item.id === need.item_id)?.count || 0)).length;
            const fullyReady = readyLines === source.items.length;
            return <article className="library-row" key={source.id}><span className={`library-icon ${fullyReady ? "ready" : "warning"}`}>{tab === "kits" ? <Boxes size={22} /> : <Sparkles size={22} />}</span><div className="library-copy"><span>{source.category ? titleCase(source.category) : "Event template"}</span><h2>{source.name}</h2><p>{source.description}</p><div className="library-items">{source.items.map((item) => <span key={item.item_id}>{item.amount}x {titleCase(item.item_id)}</span>)}</div></div><div className="library-status">{fullyReady ? <span className="available"><CheckCircle2 size={16} />Ready from stock</span> : <span className="limited"><ShieldAlert size={16} />Check availability</span>}<small>{source.items.length} equipment lines</small></div><div className="library-actions"><button className="button button-secondary" onClick={() => void preview(source, tab === "kits" ? "kit" : "template")}><Play size={16} />Preview</button>{tab === "kits" ? <button className="button button-secondary" type="button" onClick={() => startFromSuggestion(source)}><Plus size={16} />Use suggestion</button> : null}</div></article>;
          })}
        </div>
      </section>

      <Modal open={Boolean(selected)} title={selected?.source.name || "Equipment plan"} description={selected?.source.description} onClose={() => setSelected(null)} size="lg">
        {selected ? <div className="kit-plan"><div className={`plan-readiness-banner ${selected.plan.is_ready ? "ready" : "blocked"}`}>{selected.plan.is_ready ? <CheckCircle2 size={22} /> : <ShieldAlert size={22} />}<div><strong>{selected.plan.is_ready ? "Ready from current inventory" : `${selected.plan.total_missing} items missing`}</strong><span>{selected.plan.is_ready ? "Every line is available after active reservations." : "Resolve the missing stock before checking out this package."}</span></div></div><div className="plan-lines plan-lines-bordered">{selected.plan.lines.map((line) => <div key={line.item_id}><span><strong>{line.amount}x {titleCase(line.item_id)}</strong><small>{titleCase(line.type || line.capability)} · {line.source === "event" ? "Package item" : `Required by ${titleCase(line.source)}`}</small></span><span className={line.missing ? "line-missing" : "line-ready"}>{line.missing ? `${line.missing} missing` : `${line.available - line.reserved_elsewhere} free`}</span></div>)}</div><div className="modal-actions"><button className="button button-secondary" onClick={() => setSelected(null)}>Close</button>{selected.type === "kit" ? <button className="button button-primary" disabled={!selected.plan.is_ready} onClick={() => void reserve()}><PackageCheck size={17} />Check out kit</button> : null}</div></div> : null}
      </Modal>
      <Modal open={creating} title="Create equipment kit" description="Build a reusable package from inventory already available to your account." onClose={() => setCreating(false)} size="lg">
        <form className="kit-builder" onSubmit={createKit}>
          <div className="form-grid"><label>Kit name<input value={newKit.name} onChange={(event) => setNewKit({ ...newKit, name: event.target.value })} maxLength={200} required /></label><label>Description<input value={newKit.description} onChange={(event) => setNewKit({ ...newKit, description: event.target.value })} maxLength={1000} /></label><label className="form-field-wide">Handling notes<textarea rows={3} value={newKit.notes} onChange={(event) => setNewKit({ ...newKit, notes: event.target.value })} maxLength={1000} placeholder="Packing order, labels, or setup notes" /></label></div>
          <div className="kit-builder-lines"><div className="subsection-title"><h3>Kit contents</h3><button className="button button-secondary button-compact" type="button" onClick={() => setNewKit({ ...newKit, items: [...newKit.items, { id: crypto.randomUUID(), item_id: "", amount: 1 }] })}><Plus size={15} />Add item</button></div>{newKit.items.map((line) => <div className="kit-builder-line" key={line.id}><label>Inventory item<select value={line.item_id} onChange={(event) => setNewKit({ ...newKit, items: newKit.items.map((item) => item.id === line.id ? { ...item, item_id: event.target.value } : item) })} required><option value="">Choose item</option>{state!.inventory.items.map((item) => <option value={item.id} key={item.id}>{titleCase(item.id)} · {item.count} available</option>)}</select></label><label>Quantity<input type="number" min="1" max="10000" value={line.amount} onChange={(event) => setNewKit({ ...newKit, items: newKit.items.map((item) => item.id === line.id ? { ...item, amount: Number(event.target.value) } : item) })} required /></label><button className="icon-button" type="button" aria-label="Remove item" title="Remove item" disabled={newKit.items.length === 1} onClick={() => setNewKit({ ...newKit, items: newKit.items.filter((item) => item.id !== line.id) })}><Trash2 size={16} /></button></div>)}</div>
          <div className="modal-actions"><button className="button button-secondary" type="button" onClick={() => setCreating(false)}>Cancel</button><button className="button button-primary" type="submit" disabled={saving}>{saving ? "Saving kit..." : "Save kit"}</button></div>
        </form>
      </Modal>
    </div>
  );
}
