from ndr.quotes.normalization import detect_auto_close_suggestions


def test_many_missing_closers_preserve_positions_and_last_paragraph() -> None:
    text = "“未闭合的一段。\n" * 3000
    result = detect_auto_close_suggestions(text)
    assert len(result) == 2999
    assert result[0].opening_cp == 0
    assert result[0].close_cp == text.index("\n")
    assert result[-1].opening_cp == 2998 * len("“未闭合的一段。\n")


def test_closing_delimiter_before_next_opening_does_not_create_repair() -> None:
    assert detect_auto_close_suggestions("“跨段\n内容。”\n“正常。”") == ()
