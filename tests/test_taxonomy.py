from __future__ import annotations

from pathlib import Path

import pytest

from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import project_root
from save_finder.errors import TaxonomyError


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    return Taxonomy.load(project_root() / "config" / "taxonomy.yaml")


def test_taxonomy_matches_documented_counts(taxonomy: Taxonomy) -> None:
    assert taxonomy.version == 1
    assert len(taxonomy.broad) == 20
    assert len(taxonomy.specific) == 74
    assert len(taxonomy) == 94


def test_every_specific_tag_has_a_broad_parent(taxonomy: Taxonomy) -> None:
    broad = {tag.slug for tag in taxonomy.broad}
    for tag in taxonomy.specific:
        assert tag.parent in broad
        assert tag.parent in taxonomy


def test_propagate_adds_parent(taxonomy: Taxonomy) -> None:
    assert taxonomy.propagate(["receitas"]) == {"receitas", "comida-e-bebida"}
    assert taxonomy.propagate(["receitas", "carros"]) == {
        "receitas",
        "comida-e-bebida",
        "carros",
        "automotivo",
    }


def test_unknown_tag_is_rejected(taxonomy: Taxonomy) -> None:
    with pytest.raises(TaxonomyError):
        taxonomy.get("does-not-exist")
    with pytest.raises(TaxonomyError):
        taxonomy.propagate(["receitas", "does-not-exist"])


def test_duplicate_slug_is_rejected() -> None:
    raw = {
        "version": 1,
        "tags": [
            {"slug": "comida", "name": "Comida", "visual_description": "food"},
            {"slug": "comida", "name": "Comida 2", "visual_description": "food"},
        ],
    }
    with pytest.raises(TaxonomyError):
        Taxonomy.from_mapping(raw)


def test_specific_with_unknown_parent_is_rejected() -> None:
    raw = {
        "version": 1,
        "tags": [
            {"slug": "receitas", "name": "Receitas", "parent": "nao-existe"},
        ],
    }
    with pytest.raises(TaxonomyError):
        Taxonomy.from_mapping(raw)


def test_broad_tag_requires_visual_description() -> None:
    raw = {"version": 1, "tags": [{"slug": "comida", "name": "Comida"}]}
    with pytest.raises(TaxonomyError):
        Taxonomy.from_mapping(raw)


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(TaxonomyError):
        Taxonomy.load(tmp_path / "missing.yaml")
