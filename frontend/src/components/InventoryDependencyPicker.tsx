import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import type { InventoryItem } from "../types";
import { normalizeSearch } from "../lib/catalog";
import { inventoryDiagnostic, inventoryLabel, inventorySearch } from "../lib/inventoryLabel";

const RESULT_LIMIT = 50;

export function InventoryDependencyPicker({ items, value, onChange }: {
  items: InventoryItem[]; value: string; onChange: (id: string) => void;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(-1);
  const selected = items.find(item => item.id === value);
  const indexed = useMemo(() => items.map(item => ({ item, search: inventorySearch(item) })), [items]);
  const matches = useMemo(() => indexed.filter(entry => entry.search.includes(normalizeSearch(query))), [indexed, query]);
  const results = matches.slice(0, RESULT_LIMIT).map(entry => entry.item);
  useLayoutEffect(() => {
    if (!open || !root.current) return;
    const element = root.current;
    const reveal = () => element.scrollIntoView({ block: "nearest" });
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(element);
    return () => observer.disconnect();
  }, [open, results.length]);
  useEffect(() => { list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" }); }, [active]);
  function choose(item: InventoryItem) { onChange(item.id); setOpen(false); setQuery(""); input.current?.focus(); }
  function close() { setOpen(false); setQuery(""); setActive(-1); }
  return <div ref={root} className="inventory-dependency-picker" onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) close();
  }}>
    <label htmlFor={id}>Linked inventory item</label>
    <input type="hidden" name="requirement_id" value={value} />
    <div className="dependency-search-control">
      <input ref={input} id={id} role="combobox" autoComplete="off" dir="auto"
        aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-list`}
        aria-activedescendant={open && results[active] ? `${id}-option-${active}` : undefined}
        value={open ? query : value ? inventoryLabel(selected) : ""} placeholder="Search by name or model"
        onClick={() => { setOpen(true); setActive(-1); }}
        onChange={event => { setQuery(event.target.value); setOpen(true); setActive(-1); }}
        onKeyDown={event => {
          if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(); }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); setOpen(true);
            setActive(previous => !results.length ? -1 : event.key === "ArrowDown"
              ? Math.min(previous + 1, results.length - 1) : previous < 0 ? results.length - 1 : Math.max(previous - 1, 0));
          }
          if (event.key === "Enter" && open) { event.preventDefault(); if (results[active]) choose(results[active]); }
        }} />
      {value ? <button type="button" className="icon-button" aria-label="Clear selected dependency" title="Clear selected dependency"
        onClick={() => { onChange(""); close(); input.current?.focus(); }}><X size={16} /></button> : null}
    </div>
    {value ? <span className="sr-only" role="status">Selected: {inventoryLabel(selected)}</span> : null}
    {inventoryDiagnostic(selected) ? <small className="dependency-diagnostic">{inventoryDiagnostic(selected)}</small> : null}
    {open ? <div className="dependency-results">
      <div ref={list} id={`${id}-list`} role="listbox" aria-label="Inventory dependencies">
        {results.map((item, index) => <div key={item.id} id={`${id}-option-${index}`} role="option"
          aria-selected={item.id === value} data-index={index} className={active === index ? "is-active" : ""}
          onMouseDown={event => event.preventDefault()} onClick={() => choose(item)}>
          <span><strong dir="auto">{inventoryLabel(item)}</strong><small dir="auto">{inventoryDiagnostic(item)
            || [item.manufacturer, item.model, item.category_label || item.type].filter(Boolean).join(" · ")}</small></span>
          <span className="dependency-ready">{item.id === value ? <Check size={14} aria-hidden="true" /> : null}{item.count} ready</span>
        </div>)}
      </div>
      {!results.length ? <p role="status">{items.length ? "No matching items" : "No inventory items available"}</p> : null}
      {matches.length > RESULT_LIMIT ? <small role="status">Showing {RESULT_LIMIT} of {matches.length} matches. Refine your search.</small> : null}
    </div> : null}
  </div>;
}
