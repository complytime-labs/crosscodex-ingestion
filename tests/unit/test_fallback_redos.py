"""The fallback structurer's regexes run in linear time on adversarial document text.

Go's RE2 is linear by construction; Python's backtracking engine is not, so each pattern is checked
statically (no nested quantifier) and by timing. Timing compares each match against a linear baseline (a
plain character-class scan of the same text) measured in the same process, so machine load slows both
alike: a linear pattern stays within a constant factor of the baseline, while a superlinear one's factor
grows with the input and, at 500,000 characters, exceeds the bound by orders of magnitude or hits the
20-second kill. The generators drive each pattern through a long partial match that fails or backtracks
at the end, not just a first-character mismatch.
"""

import multiprocessing
import re
import re._parser as sre_parse  # the stdlib's own regex parser: a static scan that understands escapes and classes
import time
from collections.abc import Callable

import pytest

from crosscodex_ingestion.fallback import REGEXES

_N = 500_000
_KILL_AFTER_SECONDS = 20
# Linear patterns measure at most about 75 times the baseline on this machine (backtracking over a long
# [\t\n\f\r ]* run); a quadratic one measures thousands of times it already at 10,000 characters.
_MAX_BASELINE_FACTOR = 500.0
# A timing is repeated until it spans this long, so sub-microsecond matches do not measure timer noise.
_MIN_SAMPLE_SECONDS = 0.002
_BASELINE = re.compile(r"[^\x00]*")

GENERATORS: dict[str, Callable[[int], str]] = {
    "blank-lines": lambda n: "\n" * n,
    "space-tab-run": lambda n: " \t" * (n // 2),
    "digit-run": lambda n: "1" * n,
    "ivx-run": lambda n: "ivx" * (n // 3),
    "paren-a-repetition": lambda n: "(a) " * (n // 4),
    "spaces-then-clause": lambda n: " " * n + "(a) x",
    "paren-label-then-spaces": lambda n: "(a)" + " " * n,
    "number-dot-then-spaces": lambda n: "1." + " " * n,
    "roman-dot-then-spaces": lambda n: "iv." + " " * n,
    "letter-dot-then-spaces": lambda n: "a." + " " * n,
    "capital-dot-then-spaces": lambda n: "A." + " " * n,
    "article-then-spaces": lambda n: "Article" + " " * n + "x",
    "section-then-spaces": lambda n: "Section" + " " * n + "x",
    "rule-then-spaces": lambda n: "Rule" + " " * n + "x",
    "section-sign-then-spaces": lambda n: "§" + " " * n + "x",
    "digits-then-x": lambda n: "1" * n + "x",
    "dashes-then-x": lambda n: ":" + "-" * n + "x",
}

_REPEATS = (sre_parse.MAX_REPEAT, sre_parse.MIN_REPEAT, sre_parse.POSSESSIVE_REPEAT)


def _has_nested_quantifier(parsed: sre_parse.SubPattern, inside_repeat: bool = False) -> bool:
    for op, av in parsed:
        if op in _REPEATS:
            _, max_count, sub = av
            unbounded_or_multi = max_count is sre_parse.MAXREPEAT or max_count > 1
            if inside_repeat and unbounded_or_multi:
                return True
            if _has_nested_quantifier(sub, inside_repeat or unbounded_or_multi):
                return True
        elif op is sre_parse.SUBPATTERN:
            if _has_nested_quantifier(av[3], inside_repeat):
                return True
        elif op is sre_parse.BRANCH:
            if any(_has_nested_quantifier(branch, inside_repeat) for branch in av[1]):
                return True
    return False


@pytest.mark.parametrize("pattern", REGEXES, ids=lambda p: p.pattern)
def test_pattern_has_no_nested_quantifier(pattern: re.Pattern[str]) -> None:
    assert not _has_nested_quantifier(sre_parse.parse(pattern.pattern, pattern.flags))


def test_the_nested_quantifier_scan_finds_one() -> None:
    assert _has_nested_quantifier(sre_parse.parse(r"^(a+)+$"))
    assert _has_nested_quantifier(sre_parse.parse(r"^(?:\s*x|y)*$"))
    assert not _has_nested_quantifier(sre_parse.parse(r"^(xiii|xii|i)\.[ ]+(.+)\Z"))


def _seconds_per_match(pattern: re.Pattern[str], text: str) -> float:
    repeats = 1
    while True:
        start = time.perf_counter()
        for _ in range(repeats):
            pattern.match(text)
        elapsed = time.perf_counter() - start
        if elapsed >= _MIN_SAMPLE_SECONDS:
            return elapsed / repeats
        repeats *= 2


def _baseline_factor(pattern: re.Pattern[str], text: str) -> float:
    """Match time over baseline scan time on text, each the minimum of 3 measurements."""
    baseline = min(_seconds_per_match(_BASELINE, text) for _ in range(3))
    return min(_seconds_per_match(pattern, text) for _ in range(3)) / baseline


def _factors(pattern: str, flags: int, queue: "multiprocessing.Queue[dict[str, float]]") -> None:
    """Each generator's baseline factor at _N; a factor over the bound is measured once more (noise)."""
    compiled = re.compile(pattern, flags)
    factors = {}
    for name, generate in GENERATORS.items():
        text = generate(_N)
        factor = _baseline_factor(compiled, text)
        if factor >= _MAX_BASELINE_FACTOR:
            factor = min(factor, _baseline_factor(compiled, text))
        factors[name] = factor
    queue.put(factors)


@pytest.mark.parametrize(
    ("pattern", "text"),
    [(r"^a*a*b", "a" * 10_000), (r"^(a|aa)+$", "a" * 24 + "b")],
    ids=["quadratic", "exponential"],
)
def test_the_baseline_check_flags_a_superlinear_pattern(pattern: str, text: str) -> None:
    assert _baseline_factor(re.compile(pattern), text) >= _MAX_BASELINE_FACTOR


def test_the_baseline_check_passes_a_linear_pattern_on_the_same_input() -> None:
    assert _baseline_factor(re.compile(r"^a*b"), "a" * 10_000) < _MAX_BASELINE_FACTOR


@pytest.mark.parametrize("pattern", REGEXES, ids=lambda p: p.pattern)
def test_pattern_time_is_linear_on_adversarial_text(pattern: re.Pattern[str]) -> None:
    context = multiprocessing.get_context("forkserver")
    queue = context.Queue()
    child = context.Process(target=_factors, args=(pattern.pattern, pattern.flags, queue))
    child.start()
    child.join(_KILL_AFTER_SECONDS)
    if child.is_alive():
        child.kill()
        child.join()
        pytest.fail(f"{pattern.pattern!r} did not finish all generators within {_KILL_AFTER_SECONDS} s")
    assert child.exitcode == 0, f"timing child exited with {child.exitcode}"
    factors = queue.get(timeout=1)
    slow = {name: round(f) for name, f in factors.items() if f >= _MAX_BASELINE_FACTOR}
    assert not slow, f"{pattern.pattern!r} at {_N} characters, times the linear baseline: {slow}"
