"""Facts persist in the real ORM without writing the user's library."""

from sqlalchemy import select

from ndr.characters.facts import (
    CharacterIdentityFact,
    OriginalIdentitySnapshot,
    append_identity_facts,
    identity_facts_fingerprint,
    visible_identity_profile,
)
from ndr.ingest.query import load_canonical_text
from ndr.storage.models import BookCharacter, BookVersion
from ndr.storage.transactions import transaction


def test_sourced_facts_survive_session_reload_with_distinct_reveal_positions(
    migrated_client, migrated_settings
):
    client = migrated_client
    imported = client.post(
        "/api/books/import",
        files={
            "file": (
                "facts.txt",
                "第一章\n路人说：「你好。」\n后来得知他叫林舟，别名小舟。".encode(),
                "text/plain",
            ),
        },
    ).json()["data"]
    with transaction(client.app.state.session_factory) as session:
        version = session.get(BookVersion, imported["book_version_id"])
        text = load_canonical_text(migrated_settings, version)
        original = OriginalIdentitySnapshot(version.id, version.canonical_sha256, text)
        person = BookCharacter(
            book_version_id=version.id,
            canonical_name="林舟",
            aliases_json='["小舟"]',
            user_confirmed=False,
        )
        session.add(person)
        session.flush()
        person_id = person.id
        facts = []
        for kind, value in (("designation", "路人"), ("name", "林舟"), ("alias", "小舟")):
            start = text.index(value)
            facts.append(
                CharacterIdentityFact(
                    kind=kind,
                    value=value,
                    visible_from_cp=start + len(value),
                    canonical_sha256=version.canonical_sha256,
                    evidence_spans=((start, start + len(value)),),
                    source="model",
                    source_ref="roster:test",
                    accepted=True,
                )
            )
        append_identity_facts(person, version, tuple(facts), original=original)
        digest = identity_facts_fingerprint(person, version)
        assert person.version == 2 and not person.user_confirmed
    with transaction(client.app.state.session_factory) as session:
        version = session.get(BookVersion, imported["book_version_id"])
        person = session.get(BookCharacter, person_id)
        assert identity_facts_fingerprint(person, version) == digest
        early = visible_identity_profile(person, version, horizon=facts[0].visible_from_cp)
        assert early["name"] == "路人" and early["aliases"] == ()
        named = visible_identity_profile(person, version, horizon=facts[1].visible_from_cp)
        assert named["name"] == "林舟" and named["aliases"] == ("路人",)
        final = visible_identity_profile(person, version, horizon=version.canonical_length_cp)
        assert final["aliases"] == ("路人", "小舟") and not person.user_confirmed
        assert len(list(session.scalars(select(BookCharacter)))) == 1
        assert not append_identity_facts(person, version, tuple(facts), original=original)
        assert person.version == 2
