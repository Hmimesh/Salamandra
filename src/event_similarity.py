"""Bounded, deterministic planning evidence. This service performs no writes."""
from collections import defaultdict
import math
import re
from statistics import median_low

MAX_CANDIDATE_EXAMPLES = 100
MAX_RETURNED_MATCHES = 8
MAX_SUGGESTIONS = 12
MIN_SCORE = 60
WEIGHTS = {"departments": 35, "guest_count": 30, "duration_minutes": 15, "venue_type": 20}


def validate_features(value):
    if not isinstance(value, dict) or set(value) - set(WEIGHTS):
        raise ValueError("Provide supported structured event features.")
    result = {}
    for key, field in value.items():
        if field is None:
            continue
        if key == "departments":
            if not isinstance(field, list) or len(field) > 32 or any(
                not isinstance(entry, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", entry)
                for entry in field
            ):
                raise ValueError("Event departments are invalid.")
            result[key] = sorted(set(field))
        elif key == "venue_type":
            if not isinstance(field, str) or len(field) > 80 or any(ord(c) < 32 for c in field):
                raise ValueError("Venue type is invalid.")
            if field:
                result[key] = field
        else:
            maximum = 1_000_000 if key == "guest_count" else 10_080
            if type(field) is not int or not 0 <= field <= maximum:
                raise ValueError("Event numeric features are invalid.")
            if field > 0:
                result[key] = field
    return result


def score_features(current, historical):
    score, reasons = 0.0, []
    for key, weight in WEIGHTS.items():
        left, right = current.get(key), historical.get(key)
        similarity = 0.0
        if key == "departments" and isinstance(right, list) and left:
            a, b = set(left), {v for v in right if isinstance(v, str)}
            similarity = len(a & b) / len(a | b) if b else 0
        elif key in {"guest_count", "duration_minutes"}:
            if type(left) is int and type(right) in {int, float} and right > 0 and math.isfinite(right):
                similarity = min(left, right) / max(left, right)
        elif left and isinstance(right, str) and left == right:
            similarity = 1.0
        score += weight * similarity
        if similarity > 0:
            reasons.append({"dimension": key, "similarity": round(similarity, 4), "weight": weight})
    return round(score, 4), reasons


def final_requirements(example):
    corrections = example.get("corrections") or {}
    final = (corrections.get("requirements") or {}).get("final_planned")
    requirements = final if isinstance(final, list) else (example.get("proposal") or {}).get("requirements", [])
    result = {}
    for req in requirements:
        if not isinstance(req, dict):
            continue
        code, quantity = req.get("capability"), req.get("amount")
        if isinstance(code, str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,119}", code) and type(quantity) is int and 0 < quantity <= 10000:
            result[code] = result.get(code, 0) + quantity
    return {code: quantity for code, quantity in result.items() if quantity <= 10000}


def evidence_quantities(example):
    quantities = final_requirements(example)
    review = example.get("feedback") or {}
    if review.get("reuse_plan") == "no":
        return {}, ["Past operator would not reuse this plan."]
    warnings = []
    # Reviews report operational experience, not a verified corrected outcome.
    # Unresolved quantity concerns remain warnings rather than guessed adjustments.
    corrections = example.get("corrections") or {}
    corrected = (corrections.get("requirements") or {}).get("final_planned")
    changed = bool(corrections.get("changed")) and isinstance(corrected, list) and corrected != (example.get("proposal") or {}).get("requirements", [])
    if review.get("plan_fit") == "too_much" or (review.get("plan_fit") == "too_little" and not changed) or (review.get("reuse_plan") == "with_changes" and not changed):
        return {}, ["Past operator reported that this plan needs quantity review."]
    if review.get("plan_fit") == "too_little":
        warnings.append("Using operator-corrected requirements from a plan reported as too small; confirm quantities.")
    lines = (corrections.get("lines") or {}).get("final_planned")
    if not isinstance(lines, list):
        lines = (example.get("proposal") or {}).get("lines", [])
    mapping = defaultdict(set)
    for line in lines:
        if line.get("item_id") and line.get("capability"):
            mapping[line["item_id"]].add(line["capability"])
    for item in review.get("items", []):
        affected = mapping.get(item.get("item_id"), set())
        if item.get("kind") in {"missing", "additional_onsite", "unnecessary", "failed"}:
            if not affected:
                return {}, ["Past review contains equipment issues that need manual review."]
            for code in affected:
                quantities.pop(code, None)
            warnings.append("Past operators reported equipment issues; review affected requirements.")
    for field in ("missing", "unnecessary", "additional_onsite"):
        if review.get(field) == "yes" and not any(i.get("kind") == field for i in review.get("items", [])):
            return {}, ["Past review reports unresolved equipment quantities."]
    return quantities, sorted(set(warnings))


class EventSimilarityService:
    def __init__(self, store):
        self.store = store

    def rank(self, organization_id, current_event_features, limit=MAX_RETURNED_MATCHES, current_event_id=None):
        features = validate_features(current_event_features)
        if type(limit) is not int or not 1 <= limit <= MAX_RETURNED_MATCHES:
            raise ValueError("Match limit is invalid.")
        if not features:
            return []
        results = []
        for example in self.store.examples(organization_id, MAX_CANDIDATE_EXAMPLES):
            if example["event_id"] == current_event_id:
                continue
            score, reasons = score_features(features, example.get("features") or {})
            if score < MIN_SCORE or len(reasons) < 2:
                continue
            quantities, warnings = evidence_quantities(example)
            review = example.get("feedback") or {}
            results.append({"event_id": example["event_id"], "score": score, "reasons": reasons,
                            "requirements": quantities, "warnings": warnings,
                            "feedback": {k: review.get(k) for k in ("plan_fit", "reuse_plan")}})
        return sorted(results, key=lambda result: (-result["score"], result["event_id"]))[:limit]

    def suggestions(self, organization_id, features, current_event_id=None):
        features = validate_features(features)
        matches = self.rank(organization_id, features, current_event_id=current_event_id)
        quantities = defaultdict(list)
        for match in matches:
            for capability, quantity in match["requirements"].items():
                quantities[capability].append(quantity)
        suggestions = []
        for capability, values in sorted(quantities.items()):
            if capability.split(".", 1)[0] not in features.get("departments", []):
                continue
            if len(values) < 2 or len(values) * 2 < len(matches):
                continue
            # A narrow observed range is useful; broad disagreement is not.
            if max(values) > min(values) * 1.25:
                continue
            labels = {"departments": "operational departments", "guest_count": "attendance",
                      "duration_minutes": "duration", "venue_type": "venue profile"}
            dimensions = sorted({reason["dimension"] for match in matches if capability in match["requirements"]
                                 for reason in match["reasons"]})
            suggestions.append({"capability": capability, "amount": median_low(sorted(values)),
                                "minimum": min(values), "maximum": max(values), "evidence_count": len(values),
                                "reason": "Similar " + ", ".join(labels[key] for key in dimensions) + "."})
        return {"suggestions": suggestions[:MAX_SUGGESTIONS], "evidence_count": len(matches),
                "warnings": sorted({warning for match in matches for warning in match["warnings"]}),
                "dimensions": sorted({reason["dimension"] for match in matches for reason in match["reasons"]})}
