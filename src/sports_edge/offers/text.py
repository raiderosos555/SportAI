"""Text normalization for printed offers. Normalization never invents identities."""

import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

ODDS_RE = re.compile(r"^\d{1,4}[.,]\d{1,2}$")
WHOLE_ODDS_RE = re.compile(r"^[1-9]\d{1,3}$")  # e.g. "125": long odds printed without decimals
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
LINE_RE = re.compile(r"\s*\(([^()]+)\)\s*$")
SEPARATORS = (r"\s-\s", r"\s–\s", r"\s—\s", r"\svs\.?\s", r"\sv\s")
WOMEN_MARKERS = re.compile(r"\[(k|w|ż)\]|\((k|w|ż)\)|\bwomen\b|\bkobiety\b", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")
_SPECIAL = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "ß": "ss", "æ": "ae", "đ": "d"})


def parse_odds(text: str, *, allow_whole: bool = False) -> Decimal | None:
    """Printed decimal odds with a dot or a decimal comma; anything else is not odds.

    ``allow_whole`` accepts integers of two to four digits; callers enable it only for
    tokens already aligned under an odds column label.
    """
    value = text.strip()
    if not (ODDS_RE.match(value) or (allow_whole and WHOLE_ODDS_RE.match(value))):
        return None
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:  # pragma: no cover - guarded by the regex
        return None


def parse_line(text: str) -> str | None:
    """Canonical line text: totals as Decimal strings, European handicaps as 'h:a'."""
    value = text.strip().replace(",", ".")
    if re.fullmatch(r"\d+:\d+", value):
        return value
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", value):
        return format(Decimal(value).normalize(), "f")
    return None


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.translate(_SPECIAL))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def fold(text: str) -> str:
    """Case- and diacritic-insensitive key for labels and headers."""
    return _SPACES.sub(" ", strip_diacritics(text).casefold()).strip()


def split_line(name: str) -> tuple[str, str | None]:
    """'A - B (2.5)' -> ('A - B', '2.5'); unparseable parentheses stay in the name."""
    match = LINE_RE.search(name)
    if not match:
        return name.strip(), None
    line = parse_line(match.group(1))
    if line is None:
        return name.strip(), None
    return name[: match.start()].strip(), line


def split_participants(name: str) -> tuple[str, ...] | None:
    """Exactly one separator is required; ambiguous names are not split by guesswork."""
    splits = (re.split(p, name, flags=re.IGNORECASE) for p in SEPARATORS)
    found = [parts for parts in splits if len(parts) > 1]
    if len(found) != 1 or len(found[0]) != 2 or not all(p.strip() for p in found[0]):
        return None
    return tuple(p.strip() for p in found[0])


@dataclass(frozen=True)
class NameNormalizer:
    """Participant key: folded tokens, affixes removed, verified token aliases applied."""

    affixes: frozenset[str] = frozenset()
    token_aliases: dict[str, str] = field(default_factory=dict)

    def key(self, name: str) -> str:
        text = WOMEN_MARKERS.sub(" women ", name)
        text = _PUNCT.sub(" ", fold(text))
        tokens = [self.token_aliases.get(t, t) for t in text.split()]
        kept = [t for t in tokens if t not in self.affixes]
        return " ".join(kept or tokens)


def load_normalizer(path: Path) -> NameNormalizer:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    tokens = {fold(k): fold(v) for k, v in data.get("tokens", {}).items()}
    affixes = frozenset(fold(a) for a in data.get("affixes", []))
    return NameNormalizer(affixes=affixes, token_aliases=tokens)
