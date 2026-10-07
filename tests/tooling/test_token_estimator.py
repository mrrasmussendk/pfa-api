"""The regex estimator must agree, character for character, with the original loop algorithm."""

from __future__ import annotations

import pytest

from brokkr.tokenization import estimate


def _reference(text: str) -> int:
    """A literal transcription of the C# ``TokenEstimator.Estimate`` loop."""
    if not text:
        return 0
    tokens = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c.isalpha() or c == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens += max(1, (i - start + 5) // 6)
            continue
        if c.isdigit():
            start = i
            while i < n and text[i].isdigit():
                i += 1
            tokens += max(1, (i - start + 2) // 3)
            continue
        p_start = i
        while i < n and not text[i].isalnum() and not text[i].isspace() and text[i] != "_" and i - p_start < 3:
            i += 1
        tokens += 1
    return tokens


SAMPLES = [
    "",
    "x",
    "class A { }",
    "class B { int x = 123; }",
    "namespace X;\npublic sealed class Thing { public int Answer() => 42; }\n",
    "def compute_weighted_reading_score(rune_count: int, utterance_length: int) -> int:\n    return rune_count * 31 + utterance_length * 7\n",
    "a_very_long_identifier_name_that_bpe_would_split_into_many_pieces = 1234567890",
    "x >>= 3; y <<= 4; z ** 2; ... ; !== ===== ------",
    "fehu · wealth — ≈1,317 tokens",
    "    \t\n\r\n   ",
    "__init__(self, *args, **kwargs)",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_matches_reference_loop(text: str) -> None:
    assert estimate(text) == _reference(text)


def test_identifier_cost_grows_every_six_chars() -> None:
    assert estimate("abc") == 1
    assert estimate("abcdef") == 1
    assert estimate("abcdefg") == 2
    assert estimate("a" * 13) == 3


def test_digit_runs_cost_every_three_digits() -> None:
    assert estimate("1") == 1
    assert estimate("123") == 1
    assert estimate("1234") == 2


def test_punctuation_runs_merge_up_to_three() -> None:
    assert estimate("===") == 1
    assert estimate("====") == 2
    assert estimate("( )") == 2


def test_whitespace_is_free() -> None:
    assert estimate("   \n\t  ") == 0
    assert estimate("a   b") == 2
