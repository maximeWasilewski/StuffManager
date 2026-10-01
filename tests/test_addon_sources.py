"""The Home Assistant add-on builds only from the addon/ folder.

That folder carries its own copy of the application. This test fails if the
copy drifts from the sources used by Docker Compose.
"""

import filecmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _assert_same_tree(left: Path, right: Path) -> None:
    comparison = filecmp.dircmp(left, right, ignore=["__pycache__"])
    assert comparison.left_only == [], comparison.left_only
    assert comparison.right_only == [], comparison.right_only
    assert comparison.diff_files == [], comparison.diff_files
    assert comparison.funny_files == [], comparison.funny_files
    for name in comparison.subdirs:
        _assert_same_tree(left / name, right / name)


def test_addon_application_matches_the_compose_app():
    _assert_same_tree(ROOT / "app", ROOT / "addon" / "app")
    assert (ROOT / "requirements.txt").read_bytes() == (
        ROOT / "addon" / "requirements.txt"
    ).read_bytes()
