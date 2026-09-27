"""TXT 编码检测与严格解码（DEVELOPMENT.md 4.1）。

策略：

- **自动检测**：BOM 优先；否则按 ``utf-8 → gb18030 → big5`` 顺序做严格解码 + 可读性打分，
  选第一个通过者。多个通过时按优先级选择并给出 warning（confidence=low）。
- **用户显式指定**：严格解码成功就尊重用户选择（即使可读性偏低也只给 warning 与候选提示），
  因为文件可能真的含有罕见/私用码点；解码失败则报错并返回候选与有损预演。
- 一律 ``errors="strict"``：**绝不静默替换字符**，也不把 U+FFFD 写进正文。
- 预演（preview）只用于帮助用户挑选编码，标注 ``preview_is_lossy``，绝不写入数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field

CANDIDATE_ENCODINGS: tuple[str, ...] = ("utf-8", "gb18030", "big5")

_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

# 自动检测的可读性下限：低于它认为这份解码结果不像正文（拒绝而不是硬着头皮导入）。
MIN_PLAUSIBILITY = 0.999
PREVIEW_CHARS = 200


@dataclass(frozen=True)
class EncodingCandidate:
    """某个候选编码的尝试结果。"""

    encoding: str
    ok: bool
    plausibility: float
    detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "encoding": self.encoding,
            "ok": self.ok,
            "plausibility": round(self.plausibility, 4),
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class DetectionResult:
    encoding: str
    confidence: str  # high / medium / low
    text: str
    requested: str | None = None
    warnings: tuple[str, ...] = ()
    candidates: tuple[EncodingCandidate, ...] = field(default_factory=tuple)


class DecodeFailure(Exception):
    """无法用候选编码（或用户指定的编码）解出正文。"""

    def __init__(
        self,
        message: str,
        *,
        requested_encoding: str | None,
        candidates: tuple[EncodingCandidate, ...],
        preview: str,
    ) -> None:
        super().__init__(message)
        self.requested_encoding = requested_encoding
        self.candidates = candidates
        self.preview = preview

    @property
    def details(self) -> dict[str, object]:
        return {
            "requested_encoding": self.requested_encoding,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            # preview 是有损的：只帮助用户挑选编码，不是正文来源。
            "preview": self.preview,
            "preview_is_lossy": True,
        }


def plausibility(text: str) -> float:
    """正文可信度：统计控制符、私用区、替换符等“不该出现在小说正文”的码点比例。"""

    if not text:
        return 1.0
    bad = 0
    for char in text:
        if char in "\n\r\t":
            continue
        codepoint = ord(char)
        if (
            codepoint < 0x20
            or 0xD800 <= codepoint <= 0xDFFF  # 孤立代理
            or 0xE000 <= codepoint <= 0xF8FF  # 私用区
            or codepoint == 0xFFFD  # 替换符
        ):
            bad += 1
    return 1.0 - (bad / len(text))


def _ordered_candidates(preferred: str | None) -> list[str]:
    order: list[str] = []
    if preferred:
        order.append(preferred)
    for encoding in CANDIDATE_ENCODINGS:
        if encoding not in order:
            order.append(encoding)
    return order


def candidate_result(raw: bytes, encoding: str) -> EncodingCandidate:
    """严格解码并按可读性判定候选是否可用（用于自动检测与错误详情）。"""

    try:
        text = raw.decode(encoding, errors="strict")
    except (UnicodeDecodeError, LookupError) as exc:
        return EncodingCandidate(encoding=encoding, ok=False, plausibility=0.0, detail=str(exc))
    score = plausibility(text)
    if score < MIN_PLAUSIBILITY:
        return EncodingCandidate(
            encoding=encoding,
            ok=False,
            plausibility=score,
            detail=f"可读性过低（{score:.4f} < {MIN_PLAUSIBILITY}）",
        )
    return EncodingCandidate(encoding=encoding, ok=True, plausibility=score)


def lossy_preview(raw: bytes, encoding: str) -> str:
    """有损预演：仅供用户判断编码，调用方必须标注 preview_is_lossy。"""

    try:
        return raw.decode(encoding, errors="replace")[:PREVIEW_CHARS]
    except LookupError:
        return raw.decode("utf-8", errors="replace")[:PREVIEW_CHARS]


def decode_strict(raw: bytes, encoding: str) -> str:
    """严格解码；失败时抛 :class:`DecodeFailure`（带候选与 lossy 预演）。"""

    normalized = encoding.lower()
    try:
        return raw.decode(normalized, errors="strict")
    except (UnicodeDecodeError, LookupError) as exc:
        raise DecodeFailure(
            f"按 {encoding} 解码失败：{exc}",
            requested_encoding=encoding,
            candidates=tuple(
                candidate_result(raw, name) for name in _ordered_candidates(normalized)
            ),
            preview=lossy_preview(raw, normalized),
        ) from exc


def _detect_bom(raw: bytes) -> tuple[str, bytes] | None:
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return encoding, raw[len(bom) :]
    return None


def _explicit_requested(raw: bytes, requested: str) -> DetectionResult:
    normalized = requested.lower()
    try:
        text = raw.decode(normalized, errors="strict")
    except (UnicodeDecodeError, LookupError) as exc:
        raise DecodeFailure(
            f"按 {requested} 解码失败：{exc}",
            requested_encoding=requested,
            candidates=tuple(
                candidate_result(raw, name) for name in _ordered_candidates(normalized)
            ),
            preview=lossy_preview(raw, normalized),
        ) from exc

    score = plausibility(text)
    warnings: list[str] = []
    alternates = [
        name
        for name in CANDIDATE_ENCODINGS
        if name != normalized and candidate_result(raw, name).ok
    ]
    if score < MIN_PLAUSIBILITY:
        hint = f"；候选 {'、'.join(alternates)} 可严格解码" if alternates else ""
        warnings.append(
            f"按显式指定的 {normalized} 解码成功，但可读性偏低（{score:.4f}）"
            f"，请确认编码是否正确{hint}。"
        )
    elif alternates:
        warnings.append(
            f"该文件也能被 {'、'.join(alternates)} 严格解码；当前按显式指定的 {normalized} 解析。"
        )

    candidates = tuple(
        candidate_result(raw, name) for name in _ordered_candidates(normalized)
    )
    return DetectionResult(
        encoding=normalized,
        confidence="high" if score >= MIN_PLAUSIBILITY else "low",
        text=text,
        requested=requested,
        warnings=tuple(warnings),
        candidates=candidates,
    )


def detect_encoding(raw: bytes, requested: str | None = None) -> DetectionResult:
    """检测编码并返回严格解码后的正文。"""

    if not raw:
        raise DecodeFailure(
            "文件为空，无法导入。",
            requested_encoding=requested,
            candidates=(),
            preview="",
        )

    if requested:
        return _explicit_requested(raw, requested)

    bom = _detect_bom(raw)
    if bom is not None:
        encoding, body = bom
        return _explicit_requested(body, encoding)

    successful: list[tuple[str, str, float]] = []
    results: list[EncodingCandidate] = []
    for encoding in CANDIDATE_ENCODINGS:
        candidate = candidate_result(raw, encoding)
        results.append(candidate)
        if candidate.ok:
            decoded = raw.decode(encoding, errors="strict")
            successful.append((encoding, decoded, candidate.plausibility))

    if not successful:
        raise DecodeFailure(
            "无法按候选编码解出可信正文。",
            requested_encoding=None,
            candidates=tuple(results),
            preview=lossy_preview(raw, CANDIDATE_ENCODINGS[0]),
        )

    chosen_encoding, text, _score = successful[0]
    warnings: list[str] = []
    if len(successful) > 1:
        warnings.append(
            f"多种编码都能严格解码（{'、'.join(item[0] for item in successful)}），"
            f"按优先级选择 {chosen_encoding}；如需其它编码请显式指定。"
        )
        confidence = "low"
    else:
        confidence = "medium"

    return DetectionResult(
        encoding=chosen_encoding,
        confidence=confidence,
        text=text,
        warnings=tuple(warnings),
        candidates=tuple(results),
    )
