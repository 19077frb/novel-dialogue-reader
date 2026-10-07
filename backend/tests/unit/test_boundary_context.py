from dataclasses import replace

import pytest

from ndr.evaluation.boundary_context import literal_boundary_context
from ndr.evaluation.compact import Candidate, CompactTask, compile_output


def source(text, quote, *, horizon=None, initial=False):
    a = text.index(quote)
    return CompactTask(
        ("Q7",),
        {"Q7": "original-target"},
        (
            {
                "ref": "Q7",
                "kind": "target_quote",
                "start_cp": a,
                "end_cp": a + len(quote),
                "text": quote,
            },
        ),
        (
            Candidate("C2", "person-b", "周遥", existing_ref="S7"),
            Candidate("C1", "person-a", "林舟"),
        ),
        scene_ref="existing-scene",
        pov_ref="C1",
        reading_mode="initial" if initial else "reread",
        visible_horizon_cp=horizon,
    )


def test_both_edges_are_literal_and_target_and_identity_state_are_preserved():
    text = "林舟说：「出发。」\n周遥的声音传来。"
    original = source(text, "「出发。」")
    prepared = literal_boundary_context(original, text)
    task = prepared.task
    assert "".join(r["text"] for r in task.context) == text
    assert task.candidates == original.candidates and task.pov_ref == "C1"
    assert task.scene_ref == "existing-scene" and task.candidates[0].existing_ref == "S7"
    assert task.quote_ids == ("Q1",) and task.references["Q1"] == "quote:4:9"
    output = compile_output(
        {
            "labels": [
                {
                    "q": "Q1",
                    "kind": "speech",
                    "character": "C2",
                    "basis": "direct",
                    "evidence": ["G2"],
                }
            ]
        },
        task,
    )
    assert output.labels[0].speaker_ref == "S7" and not output.new_speakers


def test_initial_horizon_excludes_future_name_and_text():
    text = "旅客说：「谢谢。」\n最后他自报姓名宋梨。"
    horizon = text.index("\n")
    task = literal_boundary_context(
        source(text, "「谢谢。」", horizon=horizon, initial=True), text
    ).task
    assert all(r["end_cp"] <= horizon for r in task.context)
    assert "宋梨" not in str(task.messages())


def test_contiguous_inner_gaps_and_exactly_once_targets():
    text = "前文「甲。」中间叙述「乙。」后文"
    original = source(text, "「甲。」")
    a = text.index("「乙。」")
    original = replace(
        original,
        quote_ids=("Q7", "Q9"),
        references={**original.references, "Q9": "second-target"},
        context=original.context
        + (
            {
                "ref": "Q9",
                "kind": "target_quote",
                "start_cp": a,
                "end_cp": a + 4,
                "text": "「乙。」",
            },
        ),
    )
    task = literal_boundary_context(original, text).task
    assert "".join(r["text"] for r in task.context) == text
    assert task.quote_ids == ("Q1", "Q2")
    assert list(task.gap_next_quote.values()) == ["Q1", "Q2", None]


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence_hints": ({"ref": "Q7"},)},
        {"identity_facts": ({"candidate": "C1", "visible_from_cp": 0},)},
        {"relay": ({"ref": "Q7", "candidate": "C1"},)},
    ],
)
def test_referenced_attachments_are_not_silently_removed(changes):
    text = "「早。」"
    with pytest.raises(ValueError, match="remapping"):
        literal_boundary_context(replace(source(text, text), **changes), text)


@pytest.mark.parametrize(
    "kwargs", [{"margin": True}, {"margin": -1}, {"anchor_limit": 17}, {"anchor_limit": False}]
)
def test_limits_are_explicit_and_bounded(kwargs):
    with pytest.raises(ValueError):
        literal_boundary_context(source("「早。」", "「早。」"), "「早。」", **kwargs)


def test_nonliteral_source_and_reversed_targets_are_rejected():
    original = source("「早。」", "「早。」")
    with pytest.raises(ValueError, match="exactly"):
        literal_boundary_context(original, "「晚。」")
    text = "「甲。」「乙。」"
    a = source(text, "「乙。」")
    a = replace(
        a,
        quote_ids=("Q7", "Q8"),
        references={**a.references, "Q8": "earlier"},
        context=a.context
        + ({"ref": "Q8", "kind": "target_quote", "start_cp": 0, "end_cp": 4, "text": "「甲。」"},),
    )
    with pytest.raises(ValueError, match="ordered"):
        literal_boundary_context(a, text)


def test_parameters_and_source_change_prepared_dependency_fingerprint():
    text = "前文「早。」后文"
    a = source(text, "「早。」")
    assert (
        literal_boundary_context(a, text, margin=1).fingerprint()
        != literal_boundary_context(a, text, margin=2).fingerprint()
    )
    assert (
        literal_boundary_context(a, text, anchor_limit=0).fingerprint()
        != literal_boundary_context(a, text).fingerprint()
    )
    assert (
        literal_boundary_context(replace(a, pov_ref=None), text).fingerprint()
        != literal_boundary_context(a, text).fingerprint()
    )


def test_retained_original_anchors_are_literal_limited_and_visible():
    text = "甲的介绍\n乙的介绍\n" + "旁白" * 100 + "「早。」"
    a = source(text, "「早。」")
    records = tuple(
        {"ref": f"E{i + 1}", "kind": "overlap", "start_cp": s, "end_cp": e, "text": text[s:e]}
        for i, (s, e) in enumerate([(0, 5), (5, 10)])
    )
    a = replace(
        a,
        references={**a.references, **{r["ref"]: r["ref"] for r in records}},
        context=a.context + records,
    )
    task = literal_boundary_context(a, text, margin=0, anchor_limit=1).task
    anchors = [r for r in task.context if r["kind"] == "overlap"]
    assert len(anchors) == 1 and anchors[0]["text"] == text[5:10]
    assert not literal_boundary_context(a, text, margin=0, anchor_limit=0).task.evidence_hints
