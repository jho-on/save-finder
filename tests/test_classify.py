from __future__ import annotations

from save_finder.classify.fuse import classify_item
from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings
from save_finder.models import Item, PlatformSource, PostType


def _taxonomy() -> Taxonomy:
    return Taxonomy.load(load_settings().taxonomy_path)


def _item(caption: str | None) -> Item:
    return Item(
        id=1,
        platform="instagram",
        external_id="synthetic",
        url="https://www.instagram.com/p/synthetic/",
        source=PlatformSource.EXPORT,
        caption=caption,
        post_type=PostType.VIDEO,
    )


def test_classify_respects_taxonomy_and_emits_tags() -> None:
    taxonomy = _taxonomy()
    result = classify_item(_item("receitas de bolo"), taxonomy)

    assert result.error is None
    assert "comida-e-bebida" in result.tags
    assert "receitas" in result.tags
    assert all(tag in taxonomy for tag in result.tags)


def test_classify_emits_one_broad_tag() -> None:
    taxonomy = _taxonomy()
    broad = {tag.slug for tag in taxonomy.broad}
    result = classify_item(_item("receitas e programacao"), taxonomy)

    broad_tags = [tag for tag in result.tags if tag in broad]
    assert len(broad_tags) == 1
    assert broad_tags[0] == "comida-e-bebida"
    assert "receitas" in result.tags
    assert "programacao" not in result.tags


def test_classify_falls_back_to_outros() -> None:
    taxonomy = _taxonomy()
    result = classify_item(_item(None), taxonomy)
    assert result.tags == ("outros",)


def test_classify_never_emits_unknown_tag() -> None:
    taxonomy = _taxonomy()
    result = classify_item(_item("assunto fora de qualquer categoria"), taxonomy)
    assert all(tag in taxonomy for tag in result.tags)
