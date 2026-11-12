"""Checks that LLM text about a legal provision says nothing the provision does not.

Cheap, deterministic and strict on what matters most to a reader: numbers (rates, amounts, deadlines,
penalties) and article numbers must come from the source text, not from the model.
"""

import re

# "5 000", "5000", "0,5", "1.5", "26[1]" - a number as written in Polish legal text.
_NUMBER = re.compile(r"\d+(?:[  ]\d{3})*(?:[,.]\d+)?")
_ARTICLE = re.compile(r"\bart\.\s*(\d+[a-z]*(?:\s?\[[0-9a-z]+\]|\^[0-9a-z]+)?)", re.IGNORECASE)


def _numbers(text: str) -> set[str]:
    text = _ARTICLE.sub(" ", text)  # "art. 149" is checked as an article, not as the number 149
    return {re.sub(r"[  ]", "", n).replace(".", ",") for n in _NUMBER.findall(text)}


def _articles(text: str) -> set[str]:
    return {re.sub(r"[\s\[\]^]", "", a).lower() for a in _ARTICLE.findall(text)}


def ungrounded(generated: str, source: str) -> list[str]:
    """What `generated` states that `source` does not contain: numbers and article numbers. Empty = grounded."""
    problems = [f"liczba {n}" for n in sorted(_numbers(generated) - _numbers(source))]
    problems += [f"art. {a}" for a in sorted(_articles(generated) - _articles(source))]
    return problems
