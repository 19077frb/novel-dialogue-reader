"""Bounded transport of already-visible identities; never changes local history."""

from copy import deepcopy

from ..context.budget import estimate_tokens

IDENTITY_PROMPT_VERSION = "identity-prompt-compact-1"
MAX_RELATIONS = 6
RELATION_TOKENS = 512


def check_version(version):
    if version not in (None, IDENTITY_PROMPT_VERSION):
        raise ValueError("不支持的人物提示词版本，不能改变原任务输入继续调用")


def compact_catalog(records, *, context=""):
    """Keep all names/current descriptions, deduplicate proof, bound relations.

    Input must already be visibility-filtered. Relations remain complete values;
    rank by identities mentioned in the current context, then recency. This is
    background selection, not an assertion about speaker identity or presence.
    """
    mentioned = {
        name for p in records
        for name in (p.get("name") or p.get("canonical_name") or "", *p.get("aliases", []))
        if len(name) >= 2 and name in context
    }
    result = []
    for p in records:
        person = {k: deepcopy(p[k]) for k in (
            "character_id", "name", "canonical_name", "aliases", "description",
            "source", "user_confirmed", "confirmation_source", "name_locked",
        ) if k in p}
        names = {p.get("name") or p.get("canonical_name") or "", *p.get("aliases", [])}
        facts = {}
        field_sources = {}
        for record in p.get("identity_records", []):
            kind, value = record.get("kind"), record.get("value")
            if kind == "profile_update":
                field = record.get("field")
                current = (person.get("name", person.get("canonical_name")) if field == "name"
                           else person.get(field))
                if field == "aliases" and isinstance(value, (list, tuple)):
                    value, current = list(value), list(current or [])
                if field in {"name", "aliases", "description"} and value == current:
                    field_sources[field] = record.get("source", "unknown")
            if kind not in {"name", "alias", "designation"} or value not in names:
                continue
            facts.setdefault((kind, value, record.get("source", "unknown")), {
                "kind": kind, "value": value, "source": record.get("source", "unknown"),
            })
        if facts:
            person["identity_records"] = list(facts.values())
        if field_sources:
            person["field_sources"] = field_sources
        relations = {}
        sources = {}
        for index, record in enumerate(p.get("relations", [])):
            value = record.get("value")
            if isinstance(value, str) and value.strip():
                relations[value] = (sum(name in value for name in mentioned), index)
                sources[value] = record.get("source", "unknown")
        spent, chosen = 0, []
        for value in sorted(relations, key=relations.get, reverse=True):
            cost = estimate_tokens(value) + 16
            if cost + spent > RELATION_TOKENS:
                continue
            chosen.append({"value": value, "source": sources[value]})
            spent += cost
            if len(chosen) == MAX_RELATIONS:
                break
        if chosen:
            person["relations"] = chosen
        result.append(person)
    return result
