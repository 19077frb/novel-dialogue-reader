"""候选引语/Gap 的扫描与查询（T05）。"""

from __future__ import annotations

from .delimiters import DELIMITER_PAIRS, DelimiterPair
from .gaps import ScannedGap, build_gaps
from .ids import gap_id_for, quote_id_for
from .scanner import (
    SCANNER_VERSION,
    ScanLimits,
    ScannedQuote,
    ScanResult,
    ScanWarning,
    scan_quotes,
)
from .service import (
    ScanConflict,
    ScanOutcome,
    get_quote_detail,
    has_user_labeling,
    list_gaps,
    list_quotes,
    locate,
    scan_and_store,
)

__all__ = [
    "DELIMITER_PAIRS",
    "SCANNER_VERSION",
    "DelimiterPair",
    "ScanConflict",
    "ScanLimits",
    "ScanOutcome",
    "ScanResult",
    "ScanWarning",
    "ScannedGap",
    "ScannedQuote",
    "build_gaps",
    "gap_id_for",
    "get_quote_detail",
    "has_user_labeling",
    "list_gaps",
    "list_quotes",
    "locate",
    "quote_id_for",
    "scan_and_store",
    "scan_quotes",
]
