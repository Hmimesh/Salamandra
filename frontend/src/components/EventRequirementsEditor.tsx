import { Plus, Trash2 } from "lucide-react";
import type { CapabilityRequirement } from "../types";

export function EventRequirementsEditor({ value, onChange }: {
  value: CapabilityRequirement[];
  onChange: (value: CapabilityRequirement[]) => void;
}) {
  const update = (index: number, patch: Partial<CapabilityRequirement>) =>
    onChange(value.map((item, position) => position === index ? { ...item, ...patch } : item));
  return <fieldset className="manual-requirements">
    <legend>Equipment requirements</legend>
    {value.map((item, index) => <div className="manual-requirement-row" key={index}>
      <label>Capability<input dir="auto" value={item.capability} required maxLength={128} onChange={event => update(index, { capability: event.target.value })} /></label>
      <label>Quantity<input type="number" min="1" max="10000" step="1" required value={item.amount} onChange={event => update(index, { amount: Number(event.target.value) })} /></label>
      <label>Priority<select value={item.level} onChange={event => update(index, { level: event.target.value as CapabilityRequirement["level"] })}><option value="required">Required</option><option value="recommended">Recommended</option><option value="optional">Optional</option></select></label>
      <button type="button" className="icon-button" title="Remove requirement" aria-label={`Remove requirement ${index + 1}`} disabled={value.length === 1} onClick={() => onChange(value.filter((_, position) => position !== index))}><Trash2 size={16} /></button>
    </div>)}
    <button type="button" className="button button-secondary button-compact" onClick={() => onChange([...value, { capability: "", amount: 1, level: "required", source: "operator", min_quality: 0, required_connectors: [], attributes: {} }])}><Plus size={16} />Add requirement</button>
  </fieldset>;
}
