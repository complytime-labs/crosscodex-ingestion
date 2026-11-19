"""Tests for html_profile.py: loading, validating and registering HTML profiles."""

import hashlib
from importlib.resources import files
from pathlib import Path

import pytest

from crosscodex_ingestion.html_profile import HtmlProfile, HtmlProfileError, load_html_profiles

PACKAGED_FILE = Path(str(files("crosscodex_ingestion").joinpath("html_profiles/eur-lex-oj.yaml")))
MINIMAL = "profile_version: 1\nid: {id}\ndescription: A test profile\ndetect: p.x\n"


def _write(directory: Path, profile_id: str = "site-a", name: str | None = None, text: str | None = None) -> Path:
    path = directory / (name or f"{profile_id}.yaml")
    path.write_text(MINIMAL.format(id=profile_id) if text is None else text, encoding="utf-8")
    return path


def test_packaged_eur_lex_profile_loads():
    registry = load_html_profiles("")
    assert registry.ids() == ["eur-lex-oj"]
    assert registry.profiles[0].sha256 == hashlib.sha256(PACKAGED_FILE.read_bytes()).hexdigest()


def test_operator_profile_is_added(tmp_path):
    _write(tmp_path)
    assert load_html_profiles(str(tmp_path)).ids() == ["eur-lex-oj", "site-a"]


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("site-a.yaml", "id: [unclosed"),
        ("site-a.yaml", MINIMAL.format(id="site-a") + "detect: p.y\n"),
        ("site-a.yaml", "profile_version: 1\nid: site-a\ndescription: d\n"),
        ("site-a.yaml", MINIMAL.format(id="site-a") + "extra: 1\n"),
        ("site-a.yaml", MINIMAL.format(id="Bad_ID")),
        ("site-a.yaml", MINIMAL.format(id="site-a").replace("p.x", "p[[")),
        ("site-a.yaml", MINIMAL.format(id="site-a") + "numbered_tables: {label: '('}\n"),
        ("site-a.yaml", MINIMAL.format(id="site-a") + "remove: ['p', 'div[[']\n"),
        ("site-a.yaml", MINIMAL.format(id="site-a") + "remove: []\n"),
        ("site-a.yaml", "- a list\n"),
        ("other.yaml", MINIMAL.format(id="site-a")),
    ],
    ids=[
        "invalid-yaml",
        "duplicate-key",
        "missing-detect",
        "unknown-key",
        "bad-id",
        "bad-detect-selector",
        "bad-label-regex",
        "bad-remove-selector",
        "empty-remove",
        "not-a-mapping",
        "wrong-file-name",
    ],
)
def test_invalid_profile_refused(tmp_path, name, text):
    _write(tmp_path, name=name, text=text)
    with pytest.raises(HtmlProfileError, match=name):
        load_html_profiles(str(tmp_path))


def test_duplicate_id_across_directories_refused(tmp_path):
    operator = _write(tmp_path, "eur-lex-oj")
    with pytest.raises(HtmlProfileError, match="registered twice") as excinfo:
        load_html_profiles(str(tmp_path))
    assert str(PACKAGED_FILE) in str(excinfo.value)
    assert str(operator) in str(excinfo.value)


def test_path_must_be_a_directory(tmp_path):
    not_a_directory = _write(tmp_path)
    with pytest.raises(HtmlProfileError, match="HTML_PROFILES_PATH"):
        load_html_profiles(str(not_a_directory))


def test_non_yaml_files_are_ignored(tmp_path):
    _write(tmp_path, name="notes.yml", text="id: [unclosed")
    assert load_html_profiles(str(tmp_path)).ids() == ["eur-lex-oj"]


def test_json_round_trip():
    profile = load_html_profiles("").profiles[0]
    assert HtmlProfile.from_json(profile.to_json()) == profile
