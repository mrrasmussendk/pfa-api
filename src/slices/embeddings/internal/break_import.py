from slices.chunking.internal.chunker import split_text

def test_short_text_is_one_chunk_untouched() -> None:
    assert split_text("  Hello there.  ", 10, ) == ["Hello there."]
