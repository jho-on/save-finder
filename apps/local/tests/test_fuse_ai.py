"""AI fusion tests with fake LLM and vision providers (no models, no network)."""

from __future__ import annotations

from collections.abc import Sequence

from save_finder.classify.fuse import classify_item
from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings
from save_finder.models import Item, PlatformSource, PostType


class FakeLLM:
    name = "fake-llm"

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.response


class FakeVision:
    name = "fake-vision"

    def __init__(self, taxonomy: Taxonomy, scores: dict[str, float]) -> None:
        self.taxonomy = taxonomy
        self.scores = scores
        self.embed_calls = 0

    def embed_images(self, image_paths: Sequence) -> list[list[float]]:
        return []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.embed_calls += 1
        vectors = []
        for slug in (tag.slug for tag in self.taxonomy.broad):
            vectors.append([self.scores.get(slug, 0.0), 1.0])
        return vectors


def _taxonomy() -> Taxonomy:
    return Taxonomy.load(load_settings().taxonomy_path)


def _item(caption: str | None = None) -> Item:
    return Item(
        id=7,
        platform="instagram",
        external_id="synthetic",
        url="https://www.instagram.com/p/synthetic/",
        source=PlatformSource.EXPORT,
        caption=caption,
        post_type=PostType.VIDEO,
    )


def test_text_wins_when_transcript_is_available() -> None:
    taxonomy = _taxonomy()
    llm = FakeLLM('{"broad":"tecnologia","specific":["ia"],"confidence":0.91,"evidence":"AI demo"}')
    vision = FakeVision(taxonomy, {"comida-e-bebida": 0.9, "tecnologia": 0.1})

    result = classify_item(
        _item("demo"),
        taxonomy,
        transcript_text="a tutorial about a new AI model",
        embedding=[0.9, 1.0],
        llm=llm,
        vision=vision,
    )

    assert result.error is None
    assert result.tags == ("tecnologia", "ia")
    assert "comida-e-bebida" not in result.tags
    assert result.confidences[0] == 0.91
    assert llm.calls


def test_visual_signal_is_used_without_text() -> None:
    taxonomy = _taxonomy()
    vision = FakeVision(taxonomy, {"animais": 1.0})

    result = classify_item(
        _item(None),
        taxonomy,
        embedding=[1.0, 1.0],
        vision=vision,
    )

    assert result.tags == ("animais",)
    assert result.confidences[0] > 0.9
    assert "vision" in (result.evidence or "")


def test_unknown_tags_are_discarded() -> None:
    taxonomy = _taxonomy()
    llm = FakeLLM(
        '{"broad":"nao-existe","specific":["tag-inventada","receitas"],'
        '"confidence":0.8,"evidence":"x"}'
    )

    result = classify_item(_item("bolo"), taxonomy, llm=llm)

    assert result.tags == ("comida-e-bebida", "receitas")
    assert all(tag in taxonomy for tag in result.tags)


def test_specific_from_another_broad_is_discarded() -> None:
    taxonomy = _taxonomy()
    llm = FakeLLM('{"broad":"tecnologia","specific":["receitas"],"confidence":0.8,"evidence":"x"}')

    result = classify_item(_item("notebook"), taxonomy, llm=llm)

    assert result.tags == ("tecnologia",)
    assert "receitas" not in result.tags


def test_invalid_llm_response_falls_back_to_heuristic() -> None:
    taxonomy = _taxonomy()
    llm = FakeLLM("this is not json")

    result = classify_item(_item("receitas de bolo"), taxonomy, llm=llm)

    assert result.error is not None
    assert "comida-e-bebida" in result.tags
    assert "receitas" in result.tags


def test_prompt_lists_the_closed_taxonomy() -> None:
    taxonomy = _taxonomy()
    llm = FakeLLM('{"broad":"outros","specific":[],"confidence":0.6,"evidence":"x"}')

    classify_item(_item("assunto aleatorio"), taxonomy, llm=llm)

    system, prompt = llm.calls[0]
    assert "comida-e-bebida" in prompt
    assert "receitas (parent: comida-e-bebida)" in prompt
    assert "assunto aleatorio" in prompt
    assert "Never select a tag that the evidence says does not apply" in system
    assert "hand-sewing tools" in system
