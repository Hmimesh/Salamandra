import type { InventoryItem } from "../types";
import { normalizeSearch } from "./catalog";
import { titleCase } from "./format";

export function inventoryLabel(item?: Partial<InventoryItem>): string {
  return item?.display_name?.trim()
    || (item?.model?.trim() ? [item.manufacturer?.trim(), item.model.trim()].filter(Boolean).join(" ") : "")
    || item?.category_label?.trim()
    || (item?.type?.trim() ? titleCase(item.type) : "Unnamed inventory item");
}

export function inventoryDiagnostic(item?: Partial<InventoryItem>): string {
  return inventoryLabel(item) === "Unnamed inventory item" && item?.id ? `Item ID: ${item.id}` : "";
}

export function inventorySearch(item: InventoryItem): string {
  const aliases = item.attributes?.aliases;
  return normalizeSearch([item.display_name, item.manufacturer, item.model, item.category_label, item.type,
    ...(Array.isArray(aliases) ? aliases : [])].filter(Boolean).join(" "));
}
