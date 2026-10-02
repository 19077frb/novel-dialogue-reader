"""Bounded, redacted merge-plan diagnostics; never retain provider metadata."""

from __future__ import annotations

from ..llm.adapters import sanitize


class MergePlanError(ValueError):
    def __init__(self, issues: list[dict]) -> None:
        self.issues = issues[:50]
        super().__init__(issues[0]["message"] + "；已拒绝整份方案，未执行合并")


def character_refs(entries: list[dict]) -> dict[str, str]:
    return {f"C{index + 1}": row["character_id"] for index, row in enumerate(entries)}


def saved_model_groups(payload: dict) -> dict:
    """Keep expected plan fields before validation, without keys/usage/reasoning."""
    groups = payload.get("groups")
    if not isinstance(groups, list):
        return {"model_groups": [], "model_groups_type": type(groups).__name__}
    fields = {"target_id", "source_ids", "confidence", "reason", "merged_description"}
    saved = []
    truncated = len(groups) > 500
    for group in groups[:500]:
        if not isinstance(group, dict):
            saved.append({"invalid_type": type(group).__name__})
            continue
        record = {}
        for key in fields & group.keys():
            value = group[key]
            limit = 4096 if key in {"reason", "merged_description"} else 160
            if isinstance(value, str):
                truncated |= len(value) > limit
                record[key] = sanitize(value, limit=limit)
            elif key == "source_ids" and isinstance(value, list):
                truncated |= len(value) > 500
                record[key] = []
                for item in value[:500]:
                    truncated |= isinstance(item, str) and len(item) > 160
                    record[key].append(sanitize(item, limit=160) if isinstance(item, str)
                                       else {"invalid_type": type(item).__name__})
            elif value is None or isinstance(value, (int, float, bool)):
                record[key] = value
            else:
                record[key] = {"invalid_type": type(value).__name__}
        # The field name helps diagnose schema errors; never retain its value.
        record["extra_fields"] = [sanitize(str(key), limit=80)
                                  for key in group.keys() - fields][:50]
        saved.append(record)
    return {"model_groups": saved, "model_groups_truncated": truncated}
