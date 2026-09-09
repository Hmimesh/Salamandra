"""Bounded CSV review and conservative, model-number-aware duplicate evidence."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict

from catalog_terms import normalize_match, resolve_category

MAX_ROWS = 10_000
MAX_BYTES = 2_000_000
HEADER_ALIASES = {
    "name": ("name", "item", "equipment", "display_name", "id", "שם", "اسم", "nombre"),
    "count": ("count", "quantity", "qty", "כמות", "كمية", "cantidad"),
    "type": ("canonical_type", "type", "category", "department", "סוג", "קטגוריה", "نوع", "فئة"),
    "manufacturer": ("manufacturer", "brand", "יצרן", "الشركة المصنعة"),
    "model": ("model", "דגם", "موديل"),
    "location": ("location", "מיקום", "موقع"),
    "condition": ("condition", "status", "מצב", "حالة"),
    "info": ("info", "description", "notes", "הערות", "תיאור", "ملاحظات"),
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def parse_csv(body):
    value = body.get("csv")
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Choose a non-empty UTF-8 CSV file without null characters.")
    if len(value.encode("utf-8")) > MAX_BYTES:
        raise ValueError("CSV file must be no larger than 2 MB.")
    try:
        reader = csv.reader(io.StringIO(value.lstrip("\ufeff")), strict=True)
        headers = next(reader)
        if not headers or len(headers) > 50 or any(not h.strip() or len(h) > 200 for h in headers):
            raise ValueError("CSV needs 1-50 named columns.")
        if len(set(headers)) != len(headers):
            raise ValueError("CSV headers must be unique.")
        records = []
        for cells in reader:
            if not cells or not any(cells):
                continue
            if len(records) >= MAX_ROWS:
                raise ValueError("CSV cannot contain more than 10,000 rows.")
            if any(len(cell) > 4096 for cell in cells):
                raise ValueError("CSV fields cannot exceed 4096 characters.")
            records.append({"row": len(records) + 1, "values": dict(zip(headers, cells)),
                            "error": "Column count does not match the header." if len(cells) != len(headers) else ""})
    except (csv.Error, StopIteration) as error:
        raise ValueError("CSV is malformed. Check its quotes and header row.") from error
    if not records:
        raise ValueError("CSV contains no inventory rows.")
    folded = {normalize_match(header): header for header in headers}
    suggested = {field: next((folded[normalize_match(alias)] for alias in aliases
                             if normalize_match(alias) in folded), "") for field, aliases in HEADER_ALIASES.items()}
    mapping = body.get("column_mapping", suggested)
    if not isinstance(mapping, dict) or any(k not in HEADER_ALIASES or not isinstance(v, str)
                                            or (v and v not in headers) for k, v in mapping.items()):
        raise ValueError("Choose valid CSV columns for the mapping.")
    rows = []
    for record in records:
        row = {field: record["values"].get(mapping.get(field, ""), "") for field in HEADER_ALIASES}
        row.update(row=record["row"], error=record["error"], cells=record["values"])
        if not row["name"].strip():
            row["error"] = "Item name is required."
        try:
            if not re.fullmatch(r"[0-9]+", row["count"]):
                raise ValueError()
            row["quantity"] = int(row["count"])
            if not 1 <= row["quantity"] <= 1_000_000:
                raise ValueError()
        except ValueError:
            row["quantity"], row["error"] = 0, "Quantity must be a whole number from 1 to 1,000,000."
        if row["condition"] not in ("", "ready", "service", "retired"):
            row["error"] = "Condition must be ready, service or retired; correct or ignore its column."
        if len(row["type"]) > 200:
            row["error"] = "Category is too long (maximum 200 characters)."
        rows.append(row)
    return headers, suggested, rows


def product_key(value):
    text = normalize_match(value)
    text = re.sub(r"\belectro\s+voice\b", "ev", text)
    return "".join(text.split())


def model_numbers(item):
    return tuple(re.findall(r"\d+", item.get("model") or item.get("name", "")))


class DuplicateIndex:
    def __init__(self):
        self.names = defaultdict(list)
        self.models = defaultdict(list)

    def add(self, item):
        # Never retain another row's candidate list: duplicate chains must stay linear.
        item = {key: item[key] for key in ("target", "name", "quantity", "model", "manufacturer", "canonical_type", "expected", "match_name") if key in item}
        self.names[product_key(item.get("match_name", item["name"]))].append(item)
        if item.get("model"):
            self.models[product_key(item["model"])].append(item)

    def matches(self, item):
        candidates = self.names.get(product_key(item["name"]), [])[:40]
        if item.get("model"):
            candidates = candidates + self.models.get(product_key(item["model"]), [])[:40]
        results, seen = [], set()
        for other in candidates[:40]:
            if other["target"] in seen or other["target"] == item.get("target"):
                continue
            seen.add(other["target"])
            if model_numbers(item) and model_numbers(other) and model_numbers(item) != model_numbers(other):
                continue
            if item.get("canonical_type") and other.get("canonical_type") and item["canonical_type"] != other["canonical_type"]:
                continue
            exact = normalize_match(item["name"]) == normalize_match(other["name"])
            same_name = product_key(item["name"]) == product_key(other["name"])
            same_brand = bool(item.get("manufacturer")) and product_key(item["manufacturer"]) == product_key(other.get("manufacturer", ""))
            results.append({**other, "match": "exact" if exact else "likely" if same_name or same_brand else "possible",
                            "evidence": "Same normalized name" if exact else "Matching product/model formatting" if same_name else "Matching model; verify manufacturer"})
            if len(results) == 5:
                break
        return results


def review(body, terms, inventory):
    headers, suggested, rows = parse_csv(body)
    choices, decisions = body.get("category_choices", {}), body.get("decisions", {})
    if not isinstance(choices, dict) or not isinstance(decisions, dict):
        raise ValueError("Review choices must be objects.")
    groups = {}
    index = DuplicateIndex()
    for item in inventory:
        index.add(item)
    counts = {"create": 0, "add": 0, "skip": 0, "unresolved": 0}
    for row in rows:
        raw = row["type"] or "other"
        if raw not in groups:
            resolved = resolve_category(raw, terms) if len(raw) <= 200 else {"canonical_type": None, "source": None}
            choice = choices.get(raw, {})
            if not isinstance(choice, dict):
                raise ValueError("Category choice must be an object.")
            if "remember" in choice and not isinstance(choice["remember"], bool):
                raise ValueError("Remember mapping must be true or false.")
            action = choice.get("action", "map")
            if not isinstance(action, str) or action not in {"map", "custom", "ignore"}:
                raise ValueError("Invalid category decision.")
            code = choice.get("code", resolved["canonical_type"])
            if action == "map" and code:
                mapped = resolve_category(code, terms)
                if mapped["canonical_type"] != code:
                    raise ValueError("Choose a canonical category in this workspace.")
            label = choice.get("label", raw)
            if action == "custom" and (not isinstance(label, str) or not label.strip() or len(label) > 200):
                raise ValueError("Custom category needs a label of at most 200 characters.")
            groups[raw] = {"label": raw, "count": 0, "suggested": resolved["canonical_type"],
                           "source": resolved["source"], "action": action, "code": code,
                           "custom_label": label, "remember": bool(choice.get("remember", False))}
        group = groups[raw]
        group["count"] += 1
        row["canonical_type"] = ("draft:" + normalize_match(group["custom_label"])) if group["action"] == "custom" else group["code"]
        row["target"] = f"row:{row['row']}"
        row["candidates"] = index.matches(row)
        choice = decisions.get(str(row["row"]), {})
        if not isinstance(choice, dict):
            raise ValueError("Row decision must be an object.")
        if not isinstance(choice.get("target", ""), str) or not isinstance(choice.get("expected", ""), str):
            raise ValueError("Import target and definition token must be text.")
        action = choice.get("action", "review" if row["candidates"] or row["error"] else "new")
        if not isinstance(action, str) or action not in {"new", "add", "skip", "review"}:
            raise ValueError("Invalid duplicate decision.")
        if group["action"] == "ignore":
            action = "skip"
        row["action"] = action
        row["selected_target"] = choice.get("target", "")
        if action == "skip":
            counts["skip"] += 1
        elif row["error"] or not row["canonical_type"] or action == "review" or (action == "add" and not choice.get("target")):
            counts["unresolved"] += 1
        else:
            counts["add" if action == "add" else "create"] += 1
        if action == "new":
            index.add(row)
    return {"headers": headers, "suggested_mapping": suggested, "rows": rows,
            "categories": list(groups.values()), "counts": counts, "total": len(rows)}
