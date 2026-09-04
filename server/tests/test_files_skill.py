"""The file skills, and mostly the boundary around them.

The listing and reading tests are ordinary. The containment tests are the point
of the file: every one of them is a way a model could ask for something outside
the shared folders, and each has to fail as a sentence rather than as a
traversal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.skills.files import ListDirectory, OutsideRoots, ReadFile, SearchFiles, resolve


@dataclass
class FakeSettings:
    device_roots: tuple[Path, ...]
    device_read_chars: int = 12_000


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "Documents"
    (root / "notes").mkdir(parents=True)
    (root / ".ssh").mkdir()
    (root / "notes" / "todo.md").write_text("one\ntwo\nthree\n")
    (root / "budget.csv").write_text("a,b\n1,2\n")
    (root / "photo.jpeg").write_bytes(b"\xff\xd8\xff\xe0not really a jpeg")
    (root / ".ssh" / "id_rsa").write_text("PRIVATE KEY")
    (tmp_path / "outside.txt").write_text("should never be readable")
    return root


@pytest.fixture
def settings(tree: Path) -> FakeSettings:
    return FakeSettings(device_roots=(tree,))


# -- the boundary -------------------------------------------------------------


def test_absolute_path_outside_a_root_is_refused(settings, tmp_path):
    with pytest.raises(OutsideRoots):
        resolve(str(tmp_path / "outside.txt"), settings.device_roots)


def test_dot_dot_cannot_climb_out(settings):
    with pytest.raises(OutsideRoots):
        resolve("../outside.txt", settings.device_roots)


def test_symlink_pointing_out_is_refused(settings, tmp_path):
    """The reason resolve() resolves before it checks."""
    link = settings.device_roots[0] / "escape.txt"
    link.symlink_to(tmp_path / "outside.txt")
    with pytest.raises(OutsideRoots):
        resolve(str(link), settings.device_roots)


def test_hidden_directories_are_refused(settings):
    with pytest.raises(OutsideRoots):
        resolve(".ssh/id_rsa", settings.device_roots)


def test_no_roots_configured_refuses_everything(tree):
    with pytest.raises(OutsideRoots):
        resolve(str(tree / "budget.csv"), ())


def test_relative_paths_resolve_against_the_first_root(settings):
    got = resolve("notes/todo.md", settings.device_roots)
    assert got == settings.device_roots[0] / "notes" / "todo.md"


# -- listing ------------------------------------------------------------------


@pytest.mark.anyio
async def test_listing_with_no_path_names_the_shared_folders(settings):
    out = await ListDirectory(settings).use()
    assert str(settings.device_roots[0]) in out


@pytest.mark.anyio
async def test_listing_hides_dotfiles(settings):
    out = await ListDirectory(settings).use(str(settings.device_roots[0]))
    assert "notes/" in out
    assert "budget.csv" in out
    assert ".ssh" not in out


@pytest.mark.anyio
async def test_listing_a_file_says_to_read_it_instead(settings):
    out = await ListDirectory(settings).use("budget.csv")
    assert "read_file" in out


@pytest.mark.anyio
async def test_listing_outside_returns_a_sentence_not_an_exception(settings, tmp_path):
    out = await ListDirectory(settings).use(str(tmp_path))
    assert "outside the folders" in out


# -- reading ------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_numbers_lines_and_reports_the_total(settings):
    out = await ReadFile(settings).use("notes/todo.md")
    assert "lines 1-3 of 3" in out
    assert "1\tone" in out


@pytest.mark.anyio
async def test_read_refuses_binary_by_extension(settings):
    out = await ReadFile(settings).use("photo.jpeg")
    assert "not a text file" in out


@pytest.mark.anyio
async def test_read_truncation_says_where_to_resume(settings):
    big = settings.device_roots[0] / "big.txt"
    big.write_text("\n".join(f"line {i}" for i in range(1, 400)))
    tight = FakeSettings(device_roots=settings.device_roots, device_read_chars=80)
    out = await ReadFile(tight).use("big.txt")
    assert "offset=" in out


@pytest.mark.anyio
async def test_read_offset_starts_where_asked(settings):
    out = await ReadFile(settings).use("notes/todo.md", offset=3)
    assert "3\tthree" in out
    assert "1\tone" not in out


@pytest.mark.anyio
async def test_read_missing_file_says_so(settings):
    out = await ReadFile(settings).use("nope.md")
    assert "no file" in out.lower()


# -- searching ----------------------------------------------------------------


@pytest.mark.anyio
async def test_search_finds_by_glob(settings):
    out = await SearchFiles(settings).use("*.csv")
    assert "budget.csv" in out


@pytest.mark.anyio
async def test_bare_word_is_treated_as_contains(settings):
    out = await SearchFiles(settings).use("todo")
    assert "todo.md" in out


@pytest.mark.anyio
async def test_search_never_returns_hidden_files(settings):
    out = await SearchFiles(settings).use("*")
    assert "id_rsa" not in out


@pytest.mark.anyio
async def test_search_with_no_match_says_so(settings):
    out = await SearchFiles(settings).use("*.nothing")
    assert "Nothing matching" in out


# -- availability -------------------------------------------------------------


def test_skills_are_unavailable_without_roots():
    empty = FakeSettings(device_roots=())
    assert ListDirectory(empty).available is False
    assert ReadFile(empty).available is False


def test_skills_are_available_with_roots(settings):
    assert ListDirectory(settings).available is True


# -- the two rounds a real turn wasted ----------------------------------------


def test_a_root_named_by_its_basename_is_that_root(settings):
    """"Documents" means the root, not Documents/Documents."""
    root = settings.device_roots[0]
    assert resolve(root.name, settings.device_roots) == root
    assert resolve(f"{root.name}/budget.csv", settings.device_roots) == root / "budget.csv"


@pytest.mark.anyio
async def test_one_root_lists_itself_rather_than_announcing_itself(settings):
    out = await ListDirectory(settings).use()
    assert "budget.csv" in out
    assert "I can look in" not in out


@pytest.mark.anyio
async def test_several_roots_still_offer_the_choice(tree, tmp_path):
    second = tmp_path / "Pictures"
    second.mkdir()
    many = FakeSettings(device_roots=(tree, second))
    out = await ListDirectory(many).use()
    assert "I can look in" in out
    assert str(second) in out


# -- the road that led into the Photos library --------------------------------


def test_a_photos_library_package_is_not_walkable(settings):
    """It is an opaque database, and there is a photo skill for photos."""
    library = settings.device_roots[0] / "Photos Library.photoslibrary"
    (library / "originals").mkdir(parents=True)
    with pytest.raises(OutsideRoots):
        resolve("Photos Library.photoslibrary", settings.device_roots)
    with pytest.raises(OutsideRoots):
        resolve("Photos Library.photoslibrary/originals", settings.device_roots)


@pytest.mark.anyio
async def test_packages_are_not_listed(settings):
    (settings.device_roots[0] / "Photos Library.photoslibrary").mkdir()
    out = await ListDirectory(settings).use()
    assert "photoslibrary" not in out
