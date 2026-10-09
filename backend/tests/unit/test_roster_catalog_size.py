import json
from copy import deepcopy

from ndr.characters.service import compact_roster_catalog


def test_catalog_keeps_all_people_and_identity_values_without_repeated_historical_proofs():
    facts = [{"kind": kind, "value": value, "source": "model", "source_ref": str(index),
              "visible_from_cp": index, "evidence_spans": [[index, index + 1]]}
             for index in range(500)
             for kind, value in [("name", "林舟"), ("designation", "店长"),
                                 ("relation", "林舟打工的书店的店长")]]
    records = [{"character_id": "person", "name": "林舟", "aliases": ["小舟"],
                "description": "书店店员", "name_locked": True, "identity_records": facts,
                "relations": [dict(f) for f in facts if f["kind"] == "relation"]},
               {"character_id": "unseen", "name": "", "aliases": [], "description": ""}]
    saved = deepcopy(records)
    compact = compact_roster_catalog(records)
    assert records == saved
    assert compact[1] == records[1]
    assert compact[0]["identity_records"] == [
        {"kind": kind, "value": value, "source": "model"}
        for kind, value in [("name", "林舟"), ("designation", "店长"),
                            ("relation", "林舟打工的书店的店长")]]
    assert len(compact[0]["relations"]) == 1
    assert compact[0]["aliases"] == records[0]["aliases"]
    assert len(json.dumps(compact)) < len(json.dumps(records)) / 100


def test_catalog_does_not_merge_different_sources_or_profile_update_fields():
    records = [{"identity_records": [
        {"kind": "name", "value": "林舟", "source": "model"},
        {"kind": "name", "value": "林舟", "source": "user"},
        {"kind": "profile_update", "value": "林舟", "field": "name", "source": "user"},
        {"kind": "profile_update", "value": "林舟", "field": "description", "source": "user"},
    ]}]
    assert compact_roster_catalog(records) == records
