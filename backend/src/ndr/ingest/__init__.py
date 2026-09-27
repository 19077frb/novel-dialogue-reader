"""导入层：TXT/EPUB 到统一文档树（DEVELOPMENT.md 4.1）。"""

from __future__ import annotations

from .encoding import (
    CANDIDATE_ENCODINGS,
    DecodeFailure,
    EncodingCandidate,
    decode_strict,
    detect_encoding,
)
from .txt import NORMALIZATION_VERSION, PARSER_VERSION, ParsedTxt, parse_txt

__all__ = [
    "CANDIDATE_ENCODINGS",
    "NORMALIZATION_VERSION",
    "PARSER_VERSION",
    "DecodeFailure",
    "EncodingCandidate",
    "ParsedTxt",
    "decode_strict",
    "detect_encoding",
    "parse_txt",
]
