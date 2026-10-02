"""API models for chapter character rosters."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import ApiModel
from .enums import CharacterRosterStatus, JobState
from .inference_options import InferenceOptions


class BookCharacterOut(ApiModel):
    character_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    user_confirmed: bool = False


class CharacterDirectoryOut(BookCharacterOut):
    kind: str = "book"
    version: int = 1


class CharacterEditIn(ApiModel):
    book_version_id: str | None = None
    name: str = Field(min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list, max_length=64)
    description: str = Field(default="", max_length=512)
    expected_version: int = Field(ge=1)


class CharacterMergeIn(ApiModel):
    book_version_id: str | None = None
    target_character_id: str
    expected_version: int = Field(ge=1)
    expected_target_version: int = Field(ge=1)


class CharacterAutoMergeIn(ApiModel):
    book_version_id: str
    profile_id: str
    inference_options: InferenceOptions | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    max_total_tokens: int | None = Field(default=None, ge=1)
    run_now: bool = True


class AppliedCharacterMergeOut(ApiModel):
    target_character_id: str
    target_name: str
    source_names: list[str]
    reason: str


class CharacterMergeProposalOut(ApiModel):
    target: CharacterDirectoryOut
    sources: list[CharacterDirectoryOut]
    confidence: float
    reason: str
    # Legacy proposals did not contain a synthesized description and must be reanalysed.
    merged_description: str | None = Field(default=None, max_length=512)


class CharacterAutoMergeConfirmIn(ApiModel):
    selected_target_ids: list[str] = Field(max_length=500)


class CharacterMergeValidationIssueOut(ApiModel):
    code: str
    message: str
    group_index: int | None = None
    field: str | None = None
    character_ref: str | None = None
    related_group_index: int | None = None


class CharacterAutoMergeResultOut(ApiModel):
    job_id: str
    state: JobState
    merged_count: int = 0
    skipped_groups: int = 0
    merges: list[AppliedCharacterMergeOut] = Field(default_factory=list)
    phase: Literal["awaiting_confirmation", "applied", "discarded"] | None = None
    proposals: list[CharacterMergeProposalOut] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    unknown_usage_runs: int = 0
    last_error: str | None = None
    validation_issues: list[CharacterMergeValidationIssueOut] = Field(default_factory=list)
    created_at: str
    updated_at: str


class RosterCharacterCandidate(ApiModel):
    temp_ref: str = Field(min_length=1, max_length=64)
    character_id: str | None = None
    canonical_name: str | None = Field(default=None, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=512)
    evidence_refs: list[str] = Field(default_factory=list)
    pov_candidate: bool = False


class ChapterRosterOut(ApiModel):
    chapter_id: str
    book_version_id: str
    status: CharacterRosterStatus
    candidates: list[RosterCharacterCandidate] = Field(default_factory=list)
    confirmed_characters: list[BookCharacterOut] = Field(default_factory=list)
    pov_character_id: str | None = None
    version: int = 1


class RosterAnalyzeIn(ApiModel):
    book_version_id: str | None = None
    profile_id: str
    inference_options: InferenceOptions | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    max_input_tokens: int | None = Field(default=None, ge=1)
    run_now: bool = True


class RosterConfirmCandidateIn(ApiModel):
    temp_ref: str = Field(min_length=1, max_length=64)
    accepted: bool = True
    character_id: str | None = None
    canonical_name: str | None = Field(default=None, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=512)


class RosterConfirmIn(ApiModel):
    book_version_id: str | None = None
    candidates: list[RosterConfirmCandidateIn] = Field(min_length=1)
    pov_temp_ref: str | None = None
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_unique_refs(self) -> RosterConfirmIn:
        refs = [item.temp_ref for item in self.candidates]
        if len(refs) != len(set(refs)):
            raise ValueError("candidates 内的 temp_ref 不得重复")
        return self
