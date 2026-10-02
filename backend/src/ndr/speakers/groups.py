"""场景内匿名分组注册表。

规则：

- 展示标签（S1、S2…）按**首次可靠发言顺序**分配，只在所属场景内有意义。
- `UNKNOWN` **不创建分组**：未知对白不等于新人。
- 新声音（NEW）先建立分组；后文揭示身份时通过身份修订（merge）延迟关联，
  而不是把两组提前当成一组。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..characters.names import GENERIC_NAMES
from ..scenes.state import SceneState, SpeakerSlot


class SpeakerRegistry:
    """对 SceneState 的薄封装：负责分组查找与创建（不直接写数据库）。"""

    def __init__(self, state: SceneState) -> None:
        self.state = state

    @property
    def slots(self) -> Sequence[SpeakerSlot]:
        return tuple(self.state.participants)

    def resolve(self, ref: str | None) -> SpeakerSlot | None:
        return self.state.find(ref)

    def register_temp_speaker(
        self,
        *,
        temp_ref: str,
        first_quote_id: str,
        description: str = "",
        canonical_name: str = "",
        evidence_refs: tuple[str, ...] = (),
        character_id: str | None = None,
    ) -> SpeakerSlot:
        """把模型声明的临时人物（new1…）落成场景内的新分组。"""

        existing = self.state.find(temp_ref)
        if existing is not None and character_id and existing.character_id != character_id:
            # temp_ref belongs to this response, not the lifetime of the scene.
            # A later window may reuse new1 for a different stable person.
            existing.temp_ref = None
            existing = None
        if existing is not None:
            return existing
        if character_id:
            character = next((item for item in self.state.identity_characters
                              if item.character_id == character_id), None)
            if character is not None:
                canonical_name = character.canonical_name
        named = self.state.find_by_character(character_id)
        named = named or (
            self.state.find_by_name(canonical_name) if canonical_name not in GENERIC_NAMES else None
        )
        if named is not None and character_id and named.character_id not in (None, character_id):
            named = None
        if named is None:
            confirmed = self.state._confirmed_by_name(canonical_name)
            if confirmed is not None:
                named = self.state.find_by_character(confirmed.character_id)
        if named is not None:
            named.temp_ref = temp_ref
            if description and not named.description:
                named.description = description
            confirmed = self.state._confirmed_by_name(canonical_name)
            if confirmed is not None:
                named.character_id = confirmed.character_id
                named.canonical_name = confirmed.canonical_name
            if character_id:
                named.character_id = character_id
            self.state.remember_character(named.canonical_name, named.description)
            return named
        return self.state.add_speaker(
            first_quote_id=first_quote_id,
            description=description,
            canonical_name=canonical_name,
            evidence_refs=evidence_refs,
            temp_ref=temp_ref,
            character_id=character_id,
        )

    def ensure_local_anchor(
        self, *, first_quote_id: str, evidence_refs: tuple[str, ...] = ()
    ) -> SpeakerSlot:
        """冷启动时为首句可区分发言建立局部锚点分组。"""

        return self.state.add_speaker(
            first_quote_id=first_quote_id,
            description="首个可区分的发言者",
            evidence_refs=evidence_refs,
        )

    def label_for(self, ref: str | None) -> str | None:
        slot = self.resolve(ref)
        return slot.display_label if slot else None
