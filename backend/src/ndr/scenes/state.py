"""场景状态与转移。

状态要在窗口之间可序列化传递，且：

- **UPDATE 不切场景**；只有 BREAK 关闭当前场景并开新场景。
- **UNCERTAIN 不强制切开**：标为 `PENDING_BOUNDARY`，把未解决问题留给下一窗口。
- 长时间不发言的角色仍留在场景里（沉默不是离场），因此匿名参与者只在 BREAK 时清空。
- 用户确认过的人物是章节级身份；场景切换后必须重新进入场景状态，只是展示编号换新。
- 展示编号（S1/S2…）按**首次可靠发言顺序**分配，只在场景内有意义。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..characters.names import GENERIC_NAMES, matches_name, undecorated_name
from ..domain.enums import GapDecision, SceneStatus

SCENE_STATE_VERSION = "scene-state-6"


@dataclass(frozen=True)
class ConfirmedCharacter:
    """章节目录人物及资料来源；确认目录不等于人工核实身份。"""

    character_id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    source: str = "unknown"
    user_confirmed: bool | None = None
    confirmation_source: str = "unknown"
    relations: tuple[dict[str, Any], ...] = ()
    identity_records: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = {
            "character_id": self.character_id,
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "description": self.description,
            "source": self.source,
            "user_confirmed": self.user_confirmed,
            "confirmation_source": self.confirmation_source,
        }
        if self.relations:
            value["relations"] = [dict(item) for item in self.relations]
        if self.identity_records:
            value["identity_records"] = deepcopy(list(self.identity_records))
        return value

    def prompt_record(self) -> dict[str, Any]:
        value = self.as_dict()
        value["name"] = value.pop("canonical_name")
        return value

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ConfirmedCharacter:
        return cls(
            character_id=str(payload.get("character_id", "")),
            canonical_name=str(payload.get("canonical_name", "")),
            aliases=tuple(str(item) for item in payload.get("aliases", [])),
            description=str(payload.get("description", "")),
            source=str(payload.get("source", "unknown")),
            user_confirmed=(payload.get("user_confirmed")
                            if type(payload.get("user_confirmed")) is bool else None),
            confirmation_source=str(payload.get("confirmation_source", "unknown")),
            relations=tuple(dict(item) for item in payload.get("relations", [])
                            if isinstance(item, dict)),
            identity_records=tuple(deepcopy(item) for item in payload.get("identity_records", [])
                                   if isinstance(item, dict)),
        )


@dataclass
class SpeakerSlot:
    """场景内的一个匿名分组；可能已关联到全书稳定人物。"""

    display_label: str
    first_quote_id: str
    group_id: str | None = None
    character_id: str | None = None
    temp_ref: str | None = None
    description: str = ""
    canonical_name: str = ""
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "display_label": self.display_label,
            "first_quote_id": self.first_quote_id,
            "group_id": self.group_id,
            "character_id": self.character_id,
            "temp_ref": self.temp_ref,
            "description": self.description,
            "canonical_name": self.canonical_name,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass
class SceneTransition:
    decision: GapDecision
    gap_id: str
    closed_scene: bool = False
    opened_scene: bool = False
    pending_boundary: bool = False


@dataclass
class SceneState:
    scene_id: str | None = None
    scene_ref: str = "scene_current"
    status: SceneStatus = SceneStatus.OPEN
    start_cp: int = 0
    end_cp: int | None = None
    end_gap_id: str | None = None
    last_quote_id: str | None = None
    last_speaker_ref: str | None = None
    recent_turns: list[dict[str, str]] = field(default_factory=list)
    participants: list[SpeakerSlot] = field(default_factory=list)
    confirmed_characters: list[ConfirmedCharacter] = field(default_factory=list)
    # Refreshed from storage before a call; not duplicated in every checkpoint.
    book_characters: list[ConfirmedCharacter] = field(default_factory=list)
    pov_character_id: str | None = None
    explicit_identity: bool = False
    # Ephemeral input flag; restored jobs re-project using their frozen range version.
    projected_identity_input: bool = False
    identity_input_horizon: int | None = None
    identity_input_mode: str = "initial"
    production_expression_task: Any = None
    projected_presentation_cache: dict[str, list[dict]] = field(default_factory=dict)
    known_characters: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    version: int = 1

    # ---------- 查询 ----------

    @property
    def is_open(self) -> bool:
        return self.status in {SceneStatus.OPEN, SceneStatus.PENDING_BOUNDARY}

    def label_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for slot in self.participants:
            if slot.group_id:
                mapping[slot.group_id] = slot.display_label
            if slot.temp_ref:
                mapping[slot.temp_ref] = slot.display_label
        return mapping

    def next_label(self) -> str:
        used = {slot.display_label for slot in self.participants}
        index = 1
        while f"S{index}" in used:
            index += 1
        return f"S{index}"

    def find(self, ref: str | None) -> SpeakerSlot | None:
        if not ref:
            return None
        for slot in self.participants:
            if slot.group_id == ref or slot.temp_ref == ref or slot.display_label == ref:
                return slot
        return None

    def find_by_character(self, character_id: str | None) -> SpeakerSlot | None:
        if not character_id:
            return None
        return next(
            (slot for slot in self.participants if slot.character_id == character_id),
            None,
        )

    def _confirmed_by_name(self, value: str | None) -> ConfirmedCharacter | None:
        if self.explicit_identity:
            return None
        key = (value or "").strip().casefold()
        if not key:
            return None
        if undecorated_name(value) in GENERIC_NAMES:
            return None
        matches = [character for character in self.identity_characters if matches_name(
            value, (character.canonical_name, *character.aliases),
        )]
        return matches[0] if len(matches) == 1 else None

    @property
    def identity_characters(self) -> list[ConfirmedCharacter]:
        return list({item.character_id: item for item in
                     [*self.book_characters, *self.confirmed_characters]}.values())

    def find_by_name(self, canonical_name: str | None) -> SpeakerSlot | None:
        key = (canonical_name or "").strip().casefold()
        if not key:
            return None
        return next(
            (
                slot
                for slot in self.participants
                if slot.canonical_name.strip().casefold() == key
            ),
            None,
        )

    def remember_character(self, canonical_name: str | None, description: str = "") -> None:
        name = (canonical_name or "").strip()
        if not name:
            return
        old_description = self.known_characters.get(name, "")
        self.known_characters[name] = description.strip() or old_description

    def remember_turn(self, *, quote_id: str, slot: SpeakerSlot | None) -> None:
        """保留少量已落库轮次，帮助下一窗口延续同一组对话人。"""

        if slot is None:
            return
        self.recent_turns.append(
            {
                "quote_id": quote_id,
                "speaker_ref": slot.display_label,
                "speaker_name": slot.canonical_name or slot.description or "未说明",
            }
        )
        self.recent_turns = self.recent_turns[-8:]

    def add_speaker(
        self,
        *,
        first_quote_id: str,
        description: str = "",
        canonical_name: str = "",
        evidence_refs: tuple[str, ...] = (),
        temp_ref: str | None = None,
        character_id: str | None = None,
    ) -> SpeakerSlot:
        confirmed = self._confirmed_by_name(canonical_name)
        slot = SpeakerSlot(
            display_label=self.next_label(),
            first_quote_id=first_quote_id,
            character_id=character_id or (confirmed.character_id if confirmed else None),
            temp_ref=temp_ref,
            description=description,
            canonical_name=(confirmed.canonical_name if confirmed else canonical_name).strip(),
            evidence_refs=evidence_refs,
        )
        self.participants.append(slot)
        self.remember_character(slot.canonical_name, slot.description)
        return slot

    def sync_confirmed_participants(self) -> None:
        """Reconcile active scene slots with user-confirmed chapter identities.

        The confirmed roster is a chapter-wide identity catalog, not an
        attendance list. Missing people are therefore not inserted into every
        scene. A person enters the active scene only after the model identifies
        their first utterance as NEW; add_speaker then links the name back to
        the stable confirmed character_id.
        """

        reconciled: list[SpeakerSlot] = []
        seen_character_ids: set[str] = set()
        for slot in self.participants:
            authoritative = next(
                (
                    character
                    for character in self.confirmed_characters
                    if character.character_id == slot.character_id
                ),
                None,
            )
            if authoritative is None:
                authoritative = self._confirmed_by_name(slot.canonical_name)
            if authoritative is not None:
                # User-confirmed identity is authoritative, but do not create
                # an active participant merely because it appears in the
                # chapter roster.
                if authoritative.character_id in seen_character_ids:
                    continue
                slot.character_id = authoritative.character_id
                slot.canonical_name = authoritative.canonical_name
                if authoritative.description:
                    slot.description = authoritative.description
                seen_character_ids.add(authoritative.character_id)
            reconciled.append(slot)

        # Repair repeated labels left by older checkpoints while preserving the
        # first occurrence and active-scene order.
        unique: list[SpeakerSlot] = []
        used_labels: set[str] = set()
        for slot in reconciled:
            if slot.display_label in used_labels:
                used = {item.display_label for item in unique}
                index = 1
                while f"S{index}" in used:
                    index += 1
                slot.display_label = f"S{index}"
            used_labels.add(slot.display_label)
            unique.append(slot)
        self.participants = unique

        counts = {}
        if self.projected_identity_input:
            for character in self.identity_characters:
                counts[character.canonical_name] = counts.get(character.canonical_name, 0) + 1
        for character in self.confirmed_characters:
            if self.projected_identity_input and counts.get(character.canonical_name, 0) != 1:
                continue
            self.remember_character(character.canonical_name, character.description)

    def prompt_state(self, *, max_chars: int = 300) -> str:
        labels = "、".join(
            f"{slot.display_label}:{slot.canonical_name or slot.description or '未说明'}"
            for slot in self.participants
        ) or "（本场景还没有已建立的分组）"
        last_slot = self.find(self.last_speaker_ref)
        last_speaker = last_slot.display_label if last_slot else "未知"
        text = (
            f"场景 {self.scene_ref} 状态={self.status.value}；"
            f"最近发言人={last_speaker}；分组成员={labels}"
        )
        pov = next(
            (
                character
                for character in self.confirmed_characters
                if character.character_id == self.pov_character_id
            ),
            None,
        )
        if pov is not None:
            text += f"；本章第一视角人物={pov.canonical_name}"
        if self.recent_turns:
            turns = "→".join(
                f"{item.get('speaker_ref', '?')}:{item.get('speaker_name', '未说明')}"
                for item in self.recent_turns
            )
            text += f"；最近已确认轮次={turns}"
        if self.unresolved:
            text += f"；未解决={'、'.join(self.unresolved[:5])}"
        return text[:max_chars]

    def clear_request_aliases(self) -> None:
        for slot in self.participants:
            slot.temp_ref = None

    # ---------- 转移 ----------

    def apply_gap(
        self,
        *,
        decision: GapDecision,
        gap_id: str,
        next_quote_id: str | None,
        next_quote_start_cp: int | None,
        position_cp: int,
    ) -> SceneTransition:
        if decision is GapDecision.BREAK:
            self.status = SceneStatus.CLOSED
            self.end_cp = position_cp
            self.end_gap_id = gap_id
            self.scene_id = None
            self.start_cp = next_quote_start_cp if next_quote_start_cp is not None else position_cp
            self.end_cp = None
            self.status = SceneStatus.OPEN
            self.participants = []
            self.sync_confirmed_participants()
            self.last_speaker_ref = None
            self.recent_turns = []
            self.unresolved = []
            self.version += 1
            self.last_quote_id = next_quote_id
            return SceneTransition(
                decision=decision, gap_id=gap_id, closed_scene=True, opened_scene=True
            )

        if decision is GapDecision.UNCERTAIN:
            self.status = SceneStatus.PENDING_BOUNDARY
            if gap_id not in self.unresolved:
                self.unresolved.append(gap_id)
            return SceneTransition(decision=decision, gap_id=gap_id, pending_boundary=True)

        self.status = SceneStatus.OPEN
        self.last_quote_id = next_quote_id or self.last_quote_id
        return SceneTransition(decision=decision, gap_id=gap_id)

    # ---------- 序列化 ----------

    def snapshot(self) -> dict[str, Any]:
        return {
            "state_version": SCENE_STATE_VERSION,
            "scene_id": self.scene_id,
            "scene_ref": self.scene_ref,
            "status": self.status.value,
            "start_cp": self.start_cp,
            "end_cp": self.end_cp,
            "end_gap_id": self.end_gap_id,
            "last_quote_id": self.last_quote_id,
            "last_speaker_ref": self.last_speaker_ref,
            "recent_turns": [dict(item) for item in self.recent_turns],
            "participants": [slot.as_dict() for slot in self.participants],
            "confirmed_characters": [item.as_dict() for item in self.confirmed_characters],
            "pov_character_id": self.pov_character_id,
            "explicit_identity": self.explicit_identity,
            "known_characters": dict(self.known_characters),
            "unresolved": list(self.unresolved),
            "version": self.version,
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any] | None) -> SceneState:
        if not payload:
            return cls()
        state = cls(
            scene_id=payload.get("scene_id"),
            scene_ref=str(payload.get("scene_ref", "scene_current")),
            status=SceneStatus(payload.get("status", SceneStatus.OPEN.value)),
            start_cp=int(payload.get("start_cp", 0)),
            end_cp=payload.get("end_cp"),
            end_gap_id=payload.get("end_gap_id"),
            last_quote_id=payload.get("last_quote_id"),
            last_speaker_ref=payload.get("last_speaker_ref"),
            recent_turns=[
                {str(key): str(value) for key, value in item.items()}
                for item in payload.get("recent_turns", [])
                if isinstance(item, dict)
            ][-8:],
            confirmed_characters=[
                ConfirmedCharacter.from_dict(item)
                for item in payload.get("confirmed_characters", [])
                if isinstance(item, dict)
            ],
            pov_character_id=payload.get("pov_character_id"),
            explicit_identity=payload.get("explicit_identity") is True,
            known_characters=dict(payload.get("known_characters", {})),
            unresolved=list(payload.get("unresolved", [])),
            version=int(payload.get("version", 1)),
        )
        for slot in payload.get("participants", []):
            state.participants.append(
                SpeakerSlot(
                    display_label=str(slot.get("display_label", "S?")),
                    first_quote_id=str(slot.get("first_quote_id", "")),
                    group_id=slot.get("group_id"),
                    character_id=slot.get("character_id"),
                    temp_ref=slot.get("temp_ref"),
                    description=str(slot.get("description", "")),
                    canonical_name=str(slot.get("canonical_name", "")),
                    evidence_refs=tuple(slot.get("evidence_refs", [])),
                )
            )
        return state
