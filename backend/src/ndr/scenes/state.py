"""场景状态与转移（DEVELOPMENT.md 4.6 / PLAN 6.3）。

状态要在窗口之间可序列化传递（T10 存进 `jobs.checkpoint_json`），且：

- **UPDATE 不切场景**；只有 BREAK 关闭当前场景并开新场景。
- **UNCERTAIN 不强制切开**：标为 `PENDING_BOUNDARY`，把未解决问题留给下一窗口。
- 长时间不发言的角色仍留在场景里（沉默不是离场），因此参与者只在 BREAK 时清空。
- 展示编号（S1/S2…）按**首次可靠发言顺序**分配，只在场景内有意义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.enums import GapDecision, SceneStatus

SCENE_STATE_VERSION = "scene-state-1"


@dataclass
class SpeakerSlot:
    """场景内的一个匿名分组。"""

    display_label: str
    first_quote_id: str
    group_id: str | None = None  # 已落库的稳定 ID（新分组在提交后才拿到）
    temp_ref: str | None = None  # 模型给的临时引用（new1…），用于本次输出解析
    description: str = ""
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "display_label": self.display_label,
            "first_quote_id": self.first_quote_id,
            "group_id": self.group_id,
            "temp_ref": self.temp_ref,
            "description": self.description,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass
class SceneTransition:
    """一次 Gap 决策带来的场景变化。"""

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
    participants: list[SpeakerSlot] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    version: int = 1

    # ---------- 查询 ----------

    @property
    def is_open(self) -> bool:
        return self.status in {SceneStatus.OPEN, SceneStatus.PENDING_BOUNDARY}

    def label_map(self) -> dict[str, str]:
        """把「稳定 ID / 临时引用」都映射到展示标签，供提示与图例使用。"""

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

    def add_speaker(
        self,
        *,
        first_quote_id: str,
        description: str = "",
        evidence_refs: tuple[str, ...] = (),
        temp_ref: str | None = None,
    ) -> SpeakerSlot:
        slot = SpeakerSlot(
            display_label=self.next_label(),
            first_quote_id=first_quote_id,
            temp_ref=temp_ref,
            description=description,
            evidence_refs=evidence_refs,
        )
        self.participants.append(slot)
        return slot

    def prompt_state(self, *, max_chars: int = 300) -> str:
        """给提示用的紧凑状态文本（受 state_tokens 限制，不无限累积摘要）。"""

        labels = "、".join(
            f"{slot.display_label}:{slot.description or '未说明'}" for slot in self.participants
        ) or "（本场景还没有已建立的分组）"
        text = (
            f"场景 {self.scene_ref} 状态={self.status.value}；"
            f"最近发言={self.last_quote_id or '无'}；分组成员={labels}"
        )
        if self.unresolved:
            text += f"；未解决={'、'.join(self.unresolved[:5])}"
        return text[:max_chars]

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
        """按 Gap 决策更新状态；BREAK 时关闭当前场景并把状态复位成新场景。"""

        if decision is GapDecision.BREAK:
            self.status = SceneStatus.CLOSED
            self.end_cp = position_cp
            self.end_gap_id = gap_id
            closed = True
            self.scene_id = None
            self.start_cp = next_quote_start_cp if next_quote_start_cp is not None else position_cp
            self.end_cp = None
            self.status = SceneStatus.OPEN
            self.participants = []  # 新场景重新编号（编号只在场景内有意义）
            self.last_speaker_ref = None
            self.unresolved = []
            self.version += 1
            self.last_quote_id = next_quote_id
            return SceneTransition(
                decision=decision, gap_id=gap_id, closed_scene=closed, opened_scene=True
            )

        if decision is GapDecision.UNCERTAIN:
            self.status = SceneStatus.PENDING_BOUNDARY
            if gap_id not in self.unresolved:
                # 待定边界要留在状态里，下一窗口才能补证据（F06）
                self.unresolved.append(gap_id)
            return SceneTransition(decision=decision, gap_id=gap_id, pending_boundary=True)

        # CONTINUE / UPDATE：都不切场景（UPDATE 只是更新状态）
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
            "participants": [slot.as_dict() for slot in self.participants],
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
            unresolved=list(payload.get("unresolved", [])),
            version=int(payload.get("version", 1)),
        )
        for slot in payload.get("participants", []):
            state.participants.append(
                SpeakerSlot(
                    display_label=str(slot.get("display_label", "S?")),
                    first_quote_id=str(slot.get("first_quote_id", "")),
                    group_id=slot.get("group_id"),
                    temp_ref=slot.get("temp_ref"),
                    description=str(slot.get("description", "")),
                    evidence_refs=tuple(slot.get("evidence_refs", [])),
                )
            )
        return state
