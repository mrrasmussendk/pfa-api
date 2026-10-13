"""Print how a text would be split, by reaching straight into chunking's internals."""

import sys

from pfa.features.chunking.internal.chunker import split_text


def main() -> None:
    text = sys.stdin.read()
    for chunk in split_text(text, budget=64, count_tokens=lambda s: len(s.split())):
        print(chunk)


if __name__ == "__main__":
    main()
