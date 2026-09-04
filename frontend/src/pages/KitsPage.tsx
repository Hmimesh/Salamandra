import { Boxes, CheckCircle2, PackageCheck, Play, ShieldAlert, Sparkles } from "lucide-react";
import { useState } from "react";
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

  return (
    <div className="page kits-page">
      <PageHeader title="Kits & templates" description="Keep reusable equipment packages and event starters for your workspace." />
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
            return <article className="library-row" key={source.id}><span className={`library-icon ${fullyReady ? "ready" : "warning"}`}>{tab === "kits" ? <Boxes size={22} /> : <Sparkles size={22} />}</span><div className="library-copy"><span>{source.category ? titleCase(source.category) : "Event template"}</span><h2>{source.name}</h2><p>{source.description}</p><div className="library-items">{source.items.map((item) => <span key={item.item_id}>{item.amount}x {titleCase(item.item_id)}</span>)}</div></div><div className="library-status">{fullyReady ? <span className="available"><CheckCircle2 size={16} />Ready from stock</span> : <span className="limited"><ShieldAlert size={16} />Check availability</span>}<small>{source.items.length} equipment lines</small></div><button className="button button-secondary" onClick={() => void preview(source, tab === "kits" ? "kit" : "template")}><Play size={16} />Preview suggestion</button></article>;
          })}
        </div>
      </section>

      <Modal open={Boolean(selected)} title={selected?.source.name || "Equipment plan"} description={selected?.source.description} onClose={() => setSelected(null)} size="lg">
        {selected ? <div className="kit-plan"><div className={`plan-readiness-banner ${selected.plan.is_ready ? "ready" : "blocked"}`}>{selected.plan.is_ready ? <CheckCircle2 size={22} /> : <ShieldAlert size={22} />}<div><strong>{selected.plan.is_ready ? "Ready from current inventory" : `${selected.plan.total_missing} items missing`}</strong><span>{selected.plan.is_ready ? "Every line is available after active reservations." : "Resolve the missing stock before checking out this package."}</span></div></div><div className="plan-lines plan-lines-bordered">{selected.plan.lines.map((line) => <div key={line.item_id}><span><strong>{line.amount}x {titleCase(line.item_id)}</strong><small>{titleCase(line.type || line.capability)} · {line.source === "event" ? "Package item" : `Required by ${titleCase(line.source)}`}</small></span><span className={line.missing ? "line-missing" : "line-ready"}>{line.missing ? `${line.missing} missing` : `${line.available - line.reserved_elsewhere} free`}</span></div>)}</div><div className="modal-actions"><button className="button button-secondary" onClick={() => setSelected(null)}>Close</button>{selected.type === "kit" ? <button className="button button-primary" disabled={!selected.plan.is_ready} onClick={() => void reserve()}><PackageCheck size={17} />Check out kit</button> : null}</div></div> : null}
      </Modal>
    </div>
  );
}
