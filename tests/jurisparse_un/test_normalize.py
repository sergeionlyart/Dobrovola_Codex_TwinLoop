from __future__ import annotations

from jurisparse_un.text.normalize import normalize_text_v1


def test_normalize_text_v1_applies_all_rules() -> None:
    raw = "  First\r\n\r\n\r\nSecond\t\tLine\u00a0 \n\n  \n Third   "

    assert normalize_text_v1(raw) == "First\n\nSecond Line\n\nThird"


def test_normalize_text_v1_empty_and_whitespace_only() -> None:
    assert normalize_text_v1("") == ""
    assert normalize_text_v1(" \t\n\r\n ") == ""
