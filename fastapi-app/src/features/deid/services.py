"""De-identification strategy implementations."""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Tuple


class DeidStrategy:
    """Base class for de-identification strategies."""

    def deidentify_texts(
        self, texts: List[str], options: Dict[str, Any]
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        raise NotImplementedError


_STRATEGIES: Dict[str, DeidStrategy] = {}


def register_strategy(name: str):
    """Register a de-identification strategy."""

    def decorator(cls):
        _STRATEGIES[name] = cls()
        return cls

    return decorator


@register_strategy("default")
class RandomDigitReplacement(DeidStrategy):
    """Replace all digits with pseudo-random digits."""

    _DIGIT_RE = re.compile(r"\d+")

    def deidentify_texts(
        self, texts: List[str], options: Dict[str, Any]
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        seed = options.get("seed")
        rnd = random.Random(seed)
        mapping: Dict[str, str] = {}

        def repl(match: re.Match[str]) -> str:
            original = match.group(0)
            if original in mapping:
                return mapping[original]
            replacement = "".join(str(rnd.randint(0, 9)) for _ in original)
            mapping[original] = replacement
            return replacement

        output: List[str] = []
        for text in texts:
            output.append(self._DIGIT_RE.sub(repl, text))
        mapping_list = [
            {"type": "NUMBER", "original": k, "pseudo": v} for k, v in mapping.items()
        ]
        return output, mapping_list


def get_strategy(name: str) -> DeidStrategy:
    strategy = _STRATEGIES.get(name)
    if strategy is None:
        raise KeyError(name)
    return strategy


__all__ = ["DeidStrategy", "register_strategy", "get_strategy"]
