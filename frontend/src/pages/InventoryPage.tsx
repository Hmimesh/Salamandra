import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Boxes,
  Copy,
  Info,
  Minus,
  PackagePlus,
  Pencil,
  Plus,
  Search,
  Shapes,
  Trash2,
} from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EmptyState, Modal, PageHeader } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";
import { titleCase } from "../lib/format";
import type { InventoryItem, InventoryScope, ItemClass } from "../types";

type ItemAction = "use" | "return" | "remove";

const itemTypes = ["mixer", "pa", "microphone", "lighting", "power", "rigging", "video", "di", "backline", "stand", "cable", "case", "accessory", "furniture", "decor", "catering", "tool", "transport", "display", "barrier", "other"];

function actionCopy(action: ItemAction) {
  if (action === "use") return { title: "Check item out", button: "Mark item out", icon: ArrowUpFromLine };
  if (action === "return") return { title: "Return item", button: "Return to stock", icon: ArrowDownToLine };
  return { title: "Remove stock", button: "Remove from inventory", icon: Minus };
}

function csv(value: FormDataEntryValue | null): string[] {
  return String(value || "").split(",").map((part) => part.trim()).filter(Boolean);
}

function dependencyRules(value: FormDataEntryValue | null) {
  return String(value || "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const [capability, amount = "1", level = "required"] = line.split(",").map((part) => part.trim());
    return { capability, per_unit: Number(amount), fixed_amount: 0, level, note: "" };
  });
}

export function InventoryPage() {
  const { state, mutate } = useWorkspace();
  const navigate = useNavigate();
  const [scope, setScope] = useState<InventoryScope>("combined");
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [addOpen, setAddOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [classesOpen, setClassesOpen] = useState(false);
  const [classEditor, setClassEditor] = useState<ItemClass | "new" | null>(null);
  const [deleteClass, setDeleteClass] = useState<ItemClass | null>(null);
  const [editItem, setEditItem] = useState<InventoryItem | null>(null);
  const [action, setAction] = useState<{ type: ItemAction; item: InventoryItem } | null>(null);
  const inventory = state!.inventories[scope];
  const itemClasses = state!.item_classes.classes;
  const filtered = useMemo(() => inventory.items.filter((item) => {
    const matchesSearch = !search || `${item.id} ${item.type} ${item.info} ${item.model}`.toLowerCase().includes(search.toLowerCase());
    const matchesType = typeFilter === "all" || item.type === typeFilter;
    return matchesSearch && matchesType;
  }), [inventory.items, search, typeFilter]);
  const categories = useMemo(() => [...new Set(inventory.items.map((item) => item.type))].sort(), [inventory.items]);

  function actionScope(item: InventoryItem): "shared" | "personal" {
    if (scope !== "combined") return scope;
    return state!.inventories.shared.items.some((candidate) => candidate.id === item.id) ? "shared" : "personal";
  }

  function itemPayload(form: FormData) {
    const requirementId = String(form.get("requirement_id") || "").trim();
    return {
      id: String(form.get("id") || ""),
      type: String(form.get("type") || "other"),
      amount: Number(form.get("amount") || 1),
      scope: String(form.get("scope") || "shared"),
      info: String(form.get("info") || ""),
      class_id: String(form.get("class_id") || ""),
      manufacturer: String(form.get("manufacturer") || ""),
      model: String(form.get("model") || ""),
      condition: String(form.get("condition") || "ready"),
      quality_score: Number(form.get("quality_score") || 0),
      preference_score: Number(form.get("preference_score") || 0),
      weight_kg: Number(form.get("weight_kg") || 0),
      requirements: requirementId ? [{ item_id: requirementId, amount: Number(form.get("requirement_amount") || 1) }] : [],
    };
  }

  async function addItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await mutate("/api/inventory/items", itemPayload(new FormData(event.currentTarget)), { success: "Gear added to inventory." });
      setAddOpen(false);
    } catch {
      // The workspace provider reports the API message.
    }
  }

  async function updateItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editItem) return;
    const form = new FormData(event.currentTarget);
    try {
      await mutate("/api/inventory/items/update", { ...itemPayload(form), original_id: editItem.id, scope: actionScope(editItem) }, { success: "Equipment details updated." });
      setEditItem(null);
    } catch {
      // The workspace provider reports the API message.
    }
  }

  async function saveItemClass(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await mutate("/api/item-classes", {
        id: String(form.get("id") || ""),
        name: String(form.get("name") || ""),
        family: String(form.get("family") || "other"),
        description: String(form.get("description") || ""),
        capabilities: csv(form.get("capabilities")),
        aliases: csv(form.get("aliases")),
        connectors: csv(form.get("connectors")),
        dependency_rules: dependencyRules(form.get("dependency_rules")),
        substitute_class_ids: csv(form.get("substitute_class_ids")),
        preference_weight: Number(form.get("preference_weight") || 50),
        spare_factor: Number(form.get("spare_factor") || 0),
      }, { success: "Item class saved." });
      setClassEditor(null);
      setClassesOpen(true);
    } catch {
      // The workspace provider reports the API message.
    }
  }

  async function removeItemClass() {
    if (!deleteClass) return;
    await mutate("/api/item-classes/remove", { class_id: deleteClass.id }, { success: "Custom class removed." }).catch(() => undefined);
    setDeleteClass(null);
  }

  async function addPreset(presetId: string) {
    const targetScope = scope === "personal" ? "personal" : "shared";
    await mutate("/api/inventory/presets", { preset_id: presetId, scope: targetScope }, { success: "Preset added to inventory." }).catch(() => undefined);
  }

  async function runAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!action) return;
    const form = new FormData(event.currentTarget);
    const path = action.type === "use" ? "/api/inventory/use" : action.type === "return" ? "/api/inventory/return" : "/api/inventory/remove";
    try {
      await mutate(path, { item_id: action.item.id, amount: Number(form.get("amount") || 1), scope: actionScope(action.item) }, { success: `${titleCase(action.item.id)} updated.` });
      setAction(null);
    } catch {
      // The workspace provider reports the API message.
    }
  }

  function openClassEditor(itemClass: ItemClass | "new") {
    setClassesOpen(false);
    setClassEditor(itemClass);
  }

  function cloneClass(itemClass: ItemClass) {
    openClassEditor({ ...itemClass, id: `${itemClass.id}-custom`, name: `${itemClass.name} custom`, organization_id: state!.organization.id || "", is_system: false });
  }

  const actionDetails = action ? actionCopy(action.type) : null;
  const ActionIcon = actionDetails?.icon;
  const editingClass = classEditor === "new" ? null : classEditor;

  return (
    <div className="page inventory-page">
      <PageHeader title="Inventory" description="Work from real shared stock and personal items in your account." actions={<><button className="button button-secondary" onClick={() => setClassesOpen(true)}><Shapes size={17} />Item classes</button><button className="button button-secondary" onClick={() => setCatalogOpen(true)}><PackagePlus size={17} />Item catalog</button><button className="button button-primary" onClick={() => setAddOpen(true)}><Plus size={17} />Add item</button></>} />

      <section className="inventory-summary">
        <div><span className="summary-icon"><Boxes size={20} /></span><span><small>Item types</small><strong>{inventory.summary.unique_items}</strong></span></div>
        <div><span><small>Ready in stock</small><strong>{inventory.summary.in_stock}</strong></span></div>
        <div><span><small>Out now</small><strong>{inventory.summary.in_use}</strong></span></div>
        <div><span><small>Linked requirements</small><strong>{inventory.summary.requirements}</strong></span></div>
      </section>

      <div className="toolbar-row inventory-toolbar">
        <div className="segmented-control" aria-label="Inventory ownership">
          {(["combined", "shared", "personal"] as InventoryScope[]).map((value) => <button key={value} className={scope === value ? "active" : ""} type="button" onClick={() => setScope(value)}>{value === "combined" ? "All inventory" : value === "shared" ? "Shared inventory" : "My inventory"}</button>)}
        </div>
        <label className="search-field"><Search size={17} /><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search inventory" /></label>
        <select className="select-control" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)} aria-label="Filter by equipment type"><option value="all">All categories</option>{categories.map((category) => <option key={category} value={category}>{titleCase(category)}</option>)}</select>
      </div>

      <section className="data-section inventory-table-section">
        <div className="section-title-row"><div><h2>{scope === "combined" ? "All available inventory" : scope === "shared" ? "Shared inventory" : "My inventory"}</h2><p>{filtered.length} inventory lines</p></div><span className="result-count">{inventory.summary.in_stock} ready</span></div>
        {filtered.length ? <div className="table-scroll"><table className="operations-table inventory-table"><thead><tr><th>Gear</th><th>Class</th><th>Available</th><th>Out</th><th>Linked needs</th><th>Status</th><th>Actions</th></tr></thead><tbody>{filtered.map((item) => {
          const reserved = state!.events.active_reservations[item.id] || 0;
          const status = item.count === 0 ? "Out of stock" : reserved ? "Planned" : "Ready";
          return <tr key={item.id}><td><div className="item-name"><span className={`category-icon type-${item.type}`}>{item.id.slice(0, 1).toUpperCase()}</span><span><strong>{titleCase(item.id)}</strong><small>{[item.manufacturer, item.model, item.info].filter(Boolean).join(" · ") || "No equipment note"}</small></span>{item.info ? <span className="info-tooltip" title={item.info}><Info size={15} /></span> : null}</div></td><td><strong>{item.class_id ? titleCase(item.class_id) : titleCase(item.type)}</strong><small>{item.weight_kg ? `${item.weight_kg} kg · quality ${item.quality_score}` : item.condition}</small></td><td><strong>{item.count}</strong>{reserved ? <small>{reserved} planned across events</small> : null}</td><td>{item.in_use_count}</td><td>{item.requirements.length ? item.requirements.map((need) => `${need.amount}x ${titleCase(need.item_id)}`).join(", ") : "By class"}</td><td><span className={`stock-status stock-${status.toLowerCase().replaceAll(" ", "-")}`}>{status}</span></td><td><div className="row-actions"><button className="icon-button" title="Edit equipment" aria-label={`Edit ${item.id}`} onClick={() => setEditItem(item)}><Pencil size={16} /></button><button className="icon-button" title="Check gear out" aria-label={`Check out ${item.id}`} disabled={item.count < 1} onClick={() => setAction({ type: "use", item })}><ArrowUpFromLine size={17} /></button><button className="icon-button" title="Return gear" aria-label={`Return ${item.id}`} disabled={item.in_use_count < 1} onClick={() => setAction({ type: "return", item })}><ArrowDownToLine size={17} /></button><button className="icon-button danger-icon" title="Remove stock" aria-label={`Remove ${item.id}`} disabled={item.count < 1} onClick={() => setAction({ type: "remove", item })}><Minus size={17} /></button></div></td></tr>;
        })}</tbody></table></div> : <EmptyState title={inventory.summary.unique_items ? "No matching items" : "Your inventory is empty"} message={inventory.summary.unique_items ? "Change the filters or add an item to this inventory." : "Add an item manually, start from the catalog, or import an inventory CSV."} action={<div className="empty-actions"><button className="button button-primary" onClick={() => setAddOpen(true)}>Add inventory</button><button className="button button-secondary" onClick={() => navigate("/settings")}>Import CSV</button></div>} />}
      </section>

      <Modal open={addOpen} title="Add inventory item" description="Create sound, lighting, furniture, transport, logistics, or any other inventory item." onClose={() => setAddOpen(false)} size="lg">
        <ItemForm itemClasses={itemClasses} scope={scope} onSubmit={addItem} onCancel={() => setAddOpen(false)} />
      </Modal>

      <Modal open={Boolean(editItem)} title="Edit equipment" description={editItem ? titleCase(editItem.id) : undefined} onClose={() => setEditItem(null)} size="lg">
        {editItem ? <ItemForm item={editItem} itemClasses={itemClasses} scope={actionScope(editItem)} onSubmit={updateItem} onCancel={() => setEditItem(null)} /> : null}
      </Modal>

      <Modal open={catalogOpen} title="Item catalog" description={`Add proven presets to ${scope === "personal" ? "your inventory" : "shared inventory"}.`} onClose={() => setCatalogOpen(false)} size="lg">
        <div className="catalog-grid">{state!.presets.presets.map((preset) => <article className="catalog-item" key={preset.id}><span className={`category-icon type-${preset.type}`}>{preset.name.slice(0, 1)}</span><div><strong>{preset.name}</strong><span>{titleCase(preset.class_id || preset.type)}</span><p>{preset.description}</p><small>{preset.weight_kg ? `${preset.weight_kg} kg · quality ${preset.quality_score}` : "Ready-to-use preset"}</small></div><button className="icon-button" title={`Add ${preset.name}`} aria-label={`Add ${preset.name}`} onClick={() => void addPreset(preset.id)}><Plus size={18} /></button></article>)}</div>
        <div className="modal-actions"><button className="button button-secondary" onClick={() => setCatalogOpen(false)}>Done</button></div>
      </Modal>

      <Modal open={classesOpen} title="Item classes" description="Define what an item can do and what must travel with it." onClose={() => setClassesOpen(false)} size="lg">
        <div className="class-manager-toolbar"><span>{itemClasses.length} capability classes</span><button className="button button-primary" onClick={() => openClassEditor("new")}><Plus size={16} />New class</button></div>
        <div className="class-list">{itemClasses.map((itemClass) => <div className="class-row" key={`${itemClass.organization_id}-${itemClass.id}`}><span className="category-icon"><Shapes size={16} /></span><div><strong>{itemClass.name}</strong><small>{titleCase(itemClass.family)} · {itemClass.capabilities.join(", ")}</small><p>{itemClass.description}</p></div><span className={`class-origin ${itemClass.is_system ? "system" : "custom"}`}>{itemClass.is_system ? "Built in" : "Workspace"}</span><div className="row-actions"><button className="icon-button" title="Duplicate class" aria-label={`Duplicate ${itemClass.name}`} onClick={() => cloneClass(itemClass)}><Copy size={16} /></button>{!itemClass.is_system ? <><button className="icon-button" title="Edit class" aria-label={`Edit ${itemClass.name}`} onClick={() => openClassEditor(itemClass)}><Pencil size={16} /></button><button className="icon-button danger-icon" title="Remove class" aria-label={`Remove ${itemClass.name}`} onClick={() => setDeleteClass(itemClass)}><Trash2 size={16} /></button></> : null}</div></div>)}</div>
      </Modal>

      <Modal open={Boolean(classEditor)} title={editingClass ? "Edit item class" : "New item class"} description="Organization-owned planning behavior and linked requirements." onClose={() => { setClassEditor(null); setClassesOpen(true); }} size="lg">
        {classEditor ? <form className="form-stack" onSubmit={saveItemClass}><div className="form-grid"><label>Class ID<input name="id" defaultValue={editingClass?.id || ""} placeholder="wireless-intercom" required /></label><label>Name<input name="name" defaultValue={editingClass?.name || ""} placeholder="Wireless intercom" required /></label><label>Family<input name="family" defaultValue={editingClass?.family || "other"} placeholder="communication" required /></label><label>Preference<input name="preference_weight" type="number" min="0" max="100" defaultValue={editingClass?.preference_weight ?? 50} /></label><label className="span-2">Description<input name="description" defaultValue={editingClass?.description || ""} placeholder="What operators should know about this class" /></label><label className="span-2">Capabilities<input name="capabilities" defaultValue={editingClass?.capabilities.join(", ") || ""} placeholder="comms.beltpack, comms.listen" required /></label><label>Aliases<input name="aliases" defaultValue={editingClass?.aliases.join(", ") || ""} placeholder="beltpack, comms" /></label><label>Connectors<input name="connectors" defaultValue={editingClass?.connectors.join(", ") || ""} placeholder="xlr, power" /></label><label className="span-2">Dependencies<textarea name="dependency_rules" rows={4} defaultValue={editingClass?.dependency_rules.map((rule) => `${rule.capability}, ${rule.per_unit}, ${rule.level}`).join("\n") || ""} placeholder={"cable.xlr, 1, required\npower.battery, 2, recommended"} /></label><label>Substitute classes<input name="substitute_class_ids" defaultValue={editingClass?.substitute_class_ids.join(", ") || ""} /></label><label>Spare factor<input name="spare_factor" type="number" min="0" max="1" step="0.05" defaultValue={editingClass?.spare_factor ?? 0} /></label></div><div className="modal-actions"><button className="button button-secondary" type="button" onClick={() => { setClassEditor(null); setClassesOpen(true); }}>Cancel</button><button className="button button-primary" type="submit">Save class</button></div></form> : null}
      </Modal>

      <Modal open={Boolean(deleteClass)} title="Remove custom class" description={deleteClass?.name} onClose={() => setDeleteClass(null)} size="sm">
        <p className="confirm-copy">Existing inventory keeps its data, but automatic planning will no longer be able to resolve this class.</p><div className="modal-actions"><button className="button button-secondary" onClick={() => setDeleteClass(null)}>Cancel</button><button className="button button-danger" onClick={() => void removeItemClass()}>Remove class</button></div>
      </Modal>

      <Modal open={Boolean(action)} title={actionDetails?.title || "Update gear"} description={action ? titleCase(action.item.id) : undefined} onClose={() => setAction(null)} size="sm">
        {action && actionDetails && ActionIcon ? <form className="form-stack" onSubmit={runAction}><div className="action-summary"><ActionIcon size={22} /><span><strong>{actionDetails.button}</strong><small>{action.type === "return" ? `${action.item.in_use_count} currently out` : `${action.item.count} currently available`}</small></span></div><label>Quantity<input name="amount" type="number" min="1" max={action.type === "return" ? action.item.in_use_count : action.item.count} defaultValue="1" required /></label><div className="modal-actions"><button className="button button-secondary" type="button" onClick={() => setAction(null)}>Cancel</button><button className={`button ${action.type === "remove" ? "button-danger" : "button-primary"}`} type="submit">{actionDetails.button}</button></div></form> : null}
      </Modal>
    </div>
  );
}

function ItemForm({ item, itemClasses, scope, onSubmit, onCancel }: { item?: InventoryItem; itemClasses: ItemClass[]; scope: InventoryScope; onSubmit: (event: FormEvent<HTMLFormElement>) => void; onCancel: () => void }) {
  const requirement = item?.requirements[0];
  return <form className="form-stack" onSubmit={onSubmit}><div className="form-grid"><label>Item name<input name="id" defaultValue={item?.id || ""} placeholder="EV ZLX-15P pair A" required /></label><label>Item class<select name="class_id" defaultValue={item?.class_id || ""}><option value="">Unclassified</option>{itemClasses.map((itemClass) => <option key={`${itemClass.organization_id}-${itemClass.id}`} value={itemClass.id}>{itemClass.name}</option>)}</select></label><label>Category<select name="type" defaultValue={item?.type || "other"}>{itemTypes.map((type) => <option key={type} value={type}>{titleCase(type)}</option>)}</select></label><label>Inventory<select name="scope" defaultValue={scope === "personal" ? "personal" : "shared"}><option value="shared">Shared inventory</option><option value="personal">My inventory</option></select></label><label>Quantity<input name="amount" type="number" min="1" defaultValue={item?.count || 1} required disabled={Boolean(item)} /></label><label>Condition<select name="condition" defaultValue={item?.condition || "ready"}><option value="ready">Ready</option><option value="service">Needs service</option><option value="retired">Retired</option></select></label><label>Manufacturer<input name="manufacturer" defaultValue={item?.manufacturer || ""} placeholder="Electro-Voice" /></label><label>Model<input name="model" defaultValue={item?.model || ""} placeholder="ZLX-15P" /></label><label>Quality score<input name="quality_score" type="number" min="0" max="100" defaultValue={item?.quality_score || 70} /></label><label>Planning preference<input name="preference_score" type="number" min="0" max="100" defaultValue={item?.preference_score || 70} /></label><label>Weight (kg)<input name="weight_kg" type="number" min="0" step="0.1" defaultValue={item?.weight_kg || 0} /></label><label className="span-2">Equipment note<input name="info" defaultValue={item?.info || ""} placeholder="Shown to operators on hover and in the item row" /></label><label>Direct legacy requirement<input name="requirement_id" defaultValue={requirement?.item_id || ""} placeholder="Optional item name" /></label><label>Required quantity<input name="requirement_amount" type="number" min="1" defaultValue={requirement?.amount || 1} /></label></div><div className="modal-actions"><button className="button button-secondary" type="button" onClick={onCancel}>Cancel</button><button className="button button-primary" type="submit">{item ? "Save changes" : "Add equipment"}</button></div></form>;
}
