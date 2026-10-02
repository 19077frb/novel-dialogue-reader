"""Conservative identity matching: decorations are not new identities."""

from __future__ import annotations

import re
from collections.abc import Iterable

GENERIC_NAMES = {"男生", "女生", "同学", "男同学", "女同学", "老师", "男客", "轻浮男客", "店员"}
ROLE_NAMES = GENERIC_NAMES | {"女神", "水之女神", "女骑士", "骑士", "十字骑士", "圣骑士",
                             "冒险者", "少女", "少年", "美少女", "大祭司", "师傅", "大师"}


def is_role_name(value: str | None) -> bool:
    value = undecorated_name(value)
    return value in ROLE_NAMES or bool(re.fullmatch(
        r"(?:自称|高个子|矮个子|金发|银发|黑发|红发)?"
        r"(?:女神|女生|男生|少女|少年|美少女|女骑士|男骑士)", value,
    ))


def revealed_name(current: str | None, proposed: str | None, *, locked: bool = False) -> str | None:
    """Only upgrade a role to an explicitly proposed name, never infer one from alias order."""
    proposed = (proposed or "").strip()
    if (not locked and is_role_name(current) and valid_display_name(proposed)
            and not is_role_name(proposed) and name_key(proposed) != name_key(current)):
        return proposed
    return None


def name_key(value: str | None) -> str:
    return (value or "").strip().casefold()


def undecorated_name(value: str | None) -> str:
    """Only remove bracketed explanations; do not guess names from prose."""
    return re.split(r"[（(]", (value or "").strip(), maxsplit=1)[0].strip()


def matches_name(value: str | None, names: Iterable[str]) -> bool:
    value = undecorated_name(value)
    for name in names:
        key = name_key(name)
        if not key:
            continue
        if name_key(value) == key:
            return True
        # Explicit role + name, not a relation such as “悠太的父亲”.
        if value.endswith(name):
            prefix = value[:-len(name)]
            if re.fullmatch(r"(?:书店的|书店)?(?:女店员|男店员|店员|老师|同学)", prefix):
                return True
    return False


def valid_display_name(value: str | None) -> bool:
    name = (value or "").strip()
    return bool(name) and len(name) <= 32 and not (
        re.search(r"[（）(),，。；;\n]", name)
        or re.search(r"本章|第一人称|叙述者|向.+搭讪|的打工前辈", name)
        or re.fullmatch(r"(?:S\d+|new\d+|未知人物|未知|不确定)", name, re.IGNORECASE)
    )
