"""导入层：TXT/EPUB 到统一文档树。"""

from __future__ import annotations

from .document import (
    ParsedBook,
    ParsedChapter,
    ParsedMapping,
    ParsedNode,
    ParsedResource,
    ParsedTxt,
    collapse_whitespace,
)
from .encoding import (
    CANDIDATE_ENCODINGS,
    DecodeFailure,
    EncodingCandidate,
    decode_strict,
    detect_encoding,
)
from .epub import EPUB_PARSER_VERSION, EpubError, EpubLimits, parse_epub
from .txt import NORMALIZATION_VERSION, PARSER_VERSION, parse_txt

__all__ = [
    "CANDIDATE_ENCODINGS",
    "EPUB_PARSER_VERSION",
    "NORMALIZATION_VERSION",
    "PARSER_VERSION",
    "DecodeFailure",
    "EncodingCandidate",
    "EpubError",
    "EpubLimits",
    "ParsedBook",
    "ParsedChapter",
    "ParsedMapping",
    "ParsedNode",
    "ParsedResource",
    "ParsedTxt",
    "collapse_whitespace",
    "decode_strict",
    "detect_encoding",
    "parse_epub",
    "parse_txt",
]
