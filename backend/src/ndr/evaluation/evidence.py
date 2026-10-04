"""Position-aware original evidence retrieval; mentions are not identity merges."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from .compact import Candidate, CompactTask

EVIDENCE_VERSION = "evidence-index-2"


@dataclass(frozen=True)
class IdentityFact:
    value: str
    kind: Literal["name", "alias", "designation", "description", "relation"]
    visible_from_cp: int
    evidence_spans: tuple[tuple[int, int], ...] = ()
    source: Literal["user", "model", "source"] = "source"

    def __post_init__(self) -> None:
        if not self.value.strip() or self.visible_from_cp < 0:
            raise ValueError("Identity facts require a value and nonnegative visibility")
        if self.kind not in {"name", "alias", "designation", "description", "relation"}:
            raise ValueError("Invalid identity fact kind")
        if self.source not in {"user", "model", "source"}:
            raise ValueError("Invalid identity fact source")
        if any(a < 0 or a >= b for a, b in self.evidence_spans):
            raise ValueError("Invalid identity evidence span")
        if any(b > self.visible_from_cp for _, b in self.evidence_spans):
            raise ValueError("An identity fact cannot precede its supporting evidence")


@dataclass(frozen=True)
class EvidencePerson:
    character_id: str
    facts: tuple[IdentityFact, ...]
    # Locks are supplied by the caller's policy, not inferred from a name.
    locked: bool = False

    def visible_candidate(self, ref: str, horizon: int) -> Candidate | None:
        facts = [f for f in self.facts if f.visible_from_cp <= horizon]
        names = [f for f in facts if f.kind == "name"]
        designations = [f for f in facts if f.kind == "designation"]
        if not names and not designations:
            return None
        # A later repeated role label must not obscure an already revealed name.
        name = max(names or designations, key=lambda f: f.visible_from_cp)
        aliases = tuple(
            dict.fromkeys(
                f.value
                for f in facts
                if f.kind in {"name", "alias", "designation"} and f.value != name.value
            )
        )
        descriptions = [f.value for f in facts if f.kind == "description"]
        return Candidate(
            ref,
            self.character_id,
            name.value,
            aliases,
            "；".join(descriptions),
            visible_from_cp=name.visible_from_cp,
        )


class EvidenceIndex:
    """A single immutable chapter/version snapshot shared by concurrent windows."""

    def __init__(self, text: str, people: tuple[EvidencePerson, ...]) -> None:
        self.text = text
        self.people = people
        if len({p.character_id for p in people}) != len(people):
            raise ValueError("Duplicate stable identities in evidence snapshot")
        if any(not p.character_id for p in people):
            raise ValueError("Evidence people require stable identities")
        if any(b > len(text) for p in people for f in p.facts for _, b in f.evidence_spans):
            raise ValueError("Identity evidence is outside the original snapshot")
        self.lines: list[tuple[int, int, str]] = []
        position = 0
        for line in text.splitlines(keepends=True):
            self.lines.append((position, position + len(line), line))
            position += len(line)

    def mentions(self, person: EvidencePerson, horizon: int) -> list[tuple[int, int]]:
        names = [
            f.value
            for f in person.facts
            if f.kind in {"name", "alias", "designation"}
            and f.visible_from_cp <= horizon
            and f.value
        ]
        hits = []
        for start, end, line in self.lines:
            if end <= horizon and any(name in line for name in names):
                hits.append((start, end))
        return hits

    def task(
        self,
        spans: tuple[tuple[int, int], ...],
        *,
        margin: int = 500,
        max_candidates: int = 8,
        anchors_per_person: int = 2,
        reading_mode: Literal["initial", "reread"] = "reread",
        horizon: int | None = None,
        pov_id: str | None = None,
        retain_ids: tuple[str, ...] = (),
    ) -> CompactTask:
        if not spans or any(a < 0 or a >= b or b > len(self.text) for a, b in spans):
            raise ValueError("Invalid target spans")
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:], strict=False)):
            raise ValueError("Targets must be ordered and disjoint")
        if margin < 0 or max_candidates < 1 or anchors_per_person < 0:
            raise ValueError("Invalid retrieval policy")
        if reading_mode == "initial" and horizon is None:
            raise ValueError("Initial retrieval requires an explicit horizon")
        if reading_mode not in {"initial", "reread"} or (horizon is not None and horizon < 0):
            raise ValueError("Invalid reading mode or horizon")
        limit = len(self.text) if horizon is None else min(horizon, len(self.text))
        if spans[-1][1] > limit:
            raise ValueError("Targets exceed visible text horizon")
        start = max(0, spans[0][0] - margin)
        end = min(limit, spans[-1][1] + margin)
        ranked = []
        for person in self.people:
            candidate = person.visible_candidate("C1", limit)
            if candidate is None:
                continue
            mentions = self.mentions(person, limit)
            local = sum(start <= a < end for a, _ in mentions)
            distance = min((abs(a - spans[0][0]) for a, _ in mentions), default=len(self.text))
            priority = person.character_id == pov_id or person.character_id in retain_ids
            # No result or gold identity is used to choose candidates.
            ranked.append((not priority, -local, distance, person.character_id, person, mentions))
        ranked.sort(key=lambda row: row[:4])
        # Keep explicitly requested participants even when the soft cap is small.
        chosen = [row for row in ranked if not row[0]]
        chosen += [row for row in ranked if row[0]][: max(0, max_candidates - len(chosen))]
        candidates = tuple(
            row[4].visible_candidate(f"C{i}", limit) for i, row in enumerate(chosen, 1)
        )
        context = []
        references = {}
        gaps = {}
        quote_refs = []
        gap_num = 0

        def gap(a: int, b: int, successor: str | None) -> None:
            nonlocal gap_num
            if a >= b:
                return
            gap_num += 1
            ref = f"G{gap_num}"
            references[ref] = f"gap:{a}:{b}"
            gaps[ref] = successor
            context.append(
                {
                    "ref": ref,
                    "kind": "inner_gap",
                    "start_cp": a,
                    "end_cp": b,
                    "text": self.text[a:b],
                }
            )

        cursor = start
        for i, (a, b) in enumerate(spans, 1):
            ref = f"Q{i}"
            gap(cursor, a, ref)
            references[ref] = f"quote:{a}:{b}"
            context.append(
                {
                    "ref": ref,
                    "kind": "target_quote",
                    "start_cp": a,
                    "end_cp": b,
                    "text": self.text[a:b],
                }
            )
            quote_refs.append(ref)
            cursor = b
        gap(cursor, end, None)
        anchors = set()
        for row in chosen:
            _, _, _, _, person, mentions = row
            available = {span for span in mentions if span[1] <= start or end <= span[0]}
            # Future evidence is allowed only inside the explicit horizon; keep
            # nearby right anchors, never silently use whole-chapter summaries.
            for fact in person.facts:
                if fact.visible_from_cp <= limit:
                    available.update(
                        (a, b)
                        for a, b in fact.evidence_spans
                        if b <= limit and (b <= start or a >= end)
                    )
            # Bound all external anchors, including explicit identity proofs;
            # otherwise a long alias history silently becomes whole-book input.
            anchors.update(
                sorted(
                    available,
                    key=lambda span: (min(abs(start - span[1]), abs(span[0] - end)), span),
                )[:anchors_per_person]
            )
        for i, (a, b) in enumerate(sorted(anchors), 1):
            ref = f"E{i}"
            references[ref] = f"evidence:{a}:{b}"
            context.append(
                {"ref": ref, "kind": "overlap", "start_cp": a, "end_cp": b, "text": self.text[a:b]}
            )
        task = CompactTask(
            tuple(quote_refs),
            references,
            tuple(context),
            candidates,
            gaps,
            pov_ref=next((c.ref for c in candidates if c.character_id == pov_id), None),
            reading_mode=reading_mode,
            visible_horizon_cp=limit if reading_mode == "initial" else None,
        )
        facts = tuple(
            {
                "candidate": candidate.ref,
                "kind": fact.kind,
                "value": fact.value,
                "visible_from_cp": fact.visible_from_cp,
                "source": fact.source,
            }
            for candidate, row in zip(candidates, chosen, strict=True)
            for fact in row[4].facts
            if fact.visible_from_cp <= limit
        )
        return replace(task, evidence_hints=tuple(cues(task)), identity_facts=facts)


def blocks(
    spans: tuple[tuple[int, int], ...], size: int
) -> tuple[tuple[tuple[int, int], ...], ...]:
    if size not in {8, 16, 32}:
        raise ValueError("Registered target-count ablations are 8/16/32")
    return tuple(spans[i : i + size] for i in range(0, len(spans), size))


def cues(task: CompactTask) -> list[dict[str, object]]:
    """Retrieval hints, not speaker conclusions; preserve exceptions in prompt."""
    result = []
    for row in task.context:
        names = [
            c.ref
            for c in task.candidates
            if any(name in row["text"] for name in (c.name, *c.aliases) if name)
        ]
        result.append(
            {
                "ref": row["ref"],
                "mentioned_candidates": names,
                "speech_anchor_possible": bool(re.search(r"说|问|答|喊|叫|开口|吐槽", row["text"])),
                "address_or_self_intro_possible": bool(
                    re.search(r"你|同学|我是|我叫", row["text"])
                ),
                "entry_exit_possible": bool(re.search(r"走进|离开|进来|出去|回到", row["text"])),
            }
        )
    return result
