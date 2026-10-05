"""Score explicit accepted expression owners independently of expression kind."""

from ndr.evaluation.compact import identity_scores

VERSION = "explicit-owner-identity-first-1"
OWNER_KINDS = frozenset({"speech", "thought", "quotation"})


def score_owners(expected, rows):
    """Caller supplies known identity gold and admissibility from evidence validation.

    A correct explicit owner receives credit regardless of the three kinds.
    No owner is filled from POV, prose, reasoning content or prior answers.
    Rows are not production QuoteLabels and are never submitted to a library.
    """
    if any(not isinstance(value, str) or not value for value in expected.values()):
        raise ValueError("Explicit identity gold required")
    predictions = {}
    for row in rows:
        quote_id = row["quote_id"]
        if quote_id in predictions or quote_id not in expected:
            raise ValueError("Duplicate or unscored target")
        person = row.get("character_id")
        if person is not None and (not isinstance(person, str) or not person):
            raise ValueError("Explicit identity must be a nonempty string")
        admissible = row.get("admissible", False)
        if not isinstance(admissible, bool):
            raise ValueError("Evidence admissibility must be an explicit boolean")
        predictions[quote_id] = person if admissible and row.get("kind") in OWNER_KINDS else None
    return {
        "version": VERSION,
        "identity_scores": identity_scores(expected, predictions),
        "predictions": predictions,
        "type_is_not_identity_gate": True,
    }
