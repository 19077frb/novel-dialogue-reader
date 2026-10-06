"""Actual nonblank original references; narrative gaps are not boundary-only IDs."""

REFERENCE_POLICY_VERSION = "actual-nonblank-evidence-2"


def original_evidence_refs(task):
    # Full-source windows classify narration between quotes as gaps. Its sent
    # nonblank ref remains citable; only blank rows become boundary-only B IDs.
    # Do not add guessed IDs, metadata positions or boundary_ref to this list.
    return tuple(row["ref"] for row in task.context if row["text"].strip())
