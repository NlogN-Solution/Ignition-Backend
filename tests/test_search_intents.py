"""Search relevance and discoverability, independent of external services."""

from uuid import uuid4

import pytest

from app.services.search_intents import SearchDocument, SearchIndex, SearchUniversity, Vocabulary, normalize


def index() -> SearchIndex:
    titles = [
        ("BSc (Hons) Computer Science", "BSc (Hons)", "Computing", "Undergraduate"),
        ("MSc Computer Science", "MSc", "Computing", "Postgraduate"),
        ("MSc Advanced Computer Science", "MSc", "Computing", "Postgraduate"),
        ("BSc Computer Science with Artificial Intelligence", "BSc", "Computing", "Undergraduate"),
        ("MSc Artificial Intelligence", "MSc", "Computing", "Postgraduate"),
        ("MSc Data Science", "MSc", "Computing", "Postgraduate"),
        ("BA Business Management", "BA", "Business", "Undergraduate"),
        ("BEng Engineering", "BEng", "Engineering", "Undergraduate"),
        ("BSc Nursing", "BSc", "Health", "Undergraduate"),
        ("BA Airline Management", "BA", "Business", "Undergraduate"),
    ]
    return SearchIndex(
        Vocabulary(
            universities=[SearchUniversity(slug="york-st-john", name="York St John University", city="York")],
            documents=[
                SearchDocument(
                    id=str(uuid4()),
                    title=title,
                    qualification=award,
                    subject=subject,
                    level=level,
                    university="york-st-john",
                    university_name="York St John University",
                    city="York",
                    campus="London/Manchester",
                )
                for title, award, subject, level in titles
            ],
        )
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("computer", "Computer Science"),
        ("comp sci", "Computer Science"),
        ("computor scince", "Computer Science"),
        ("business", "Business"),
        ("bussiness", "Business"),
        ("ai", "Artificial Intelligence"),
        ("artificial intelligence", "Artificial Intelligence"),
        ("engineering", "Engineering"),
        ("msc", "MSc"),
        ("london", "London"),
        ("manchester", "Manchester"),
    ],
)
def test_best_suggestion(query: str, expected: str):
    suggestions = index().suggestions(query)
    assert suggestions[0].label == expected
    assert len(suggestions) <= 6
    assert len({normalize(s.label) for s in suggestions}) == len(suggestions)
    assert all(s.destination.path == "/courses" for s in suggestions)


def test_empty_short_and_unrelated_queries():
    engine = index()
    assert [s.label for s in engine.suggestions("")] == [
        "Computer Science",
        "Business",
        "Engineering",
        "MSc",
        "Artificial Intelligence",
        "Nursing",
    ]
    assert not engine.suggestions("c")
    assert not engine.suggestions("zzzxxyy")
    assert not engine.matches("zzzxxyy")


def test_exact_university_is_the_only_detail_intent():
    engine = index()
    exact = engine.suggestions("  york ST john university ")
    assert exact[0].exact_entity and exact[0].entity_slug == "york-st-john"
    assert exact[1].destination.university == "york-st-john"
    assert not any(s.exact_entity for s in engine.suggestions("York"))


def test_two_character_prefix_starts_discovery():
    engine = index()
    assert any(item.label == "Computer Science" for item in engine.suggestions("co"))
    assert engine.suggestions("bu")[0].label == "Business"
    assert engine.matches("co")


def test_results_require_all_meaningful_tokens():
    engine = index()
    matches = engine.matches("computer science")
    titles = [d.title for d in engine.vocabulary.documents if d.id in matches]
    assert len(titles) == 4
    assert "MSc Data Science" not in titles
    assert len(engine.matches("computor scince")) == 4
    assert len(engine.matches("comp sci")) == 4
    assert len(engine.matches("ai")) == 2
    assert len(engine.matches("MSc")) == 4


def test_normalization_and_published_data_only_popular():
    assert normalize("  Computer -- SCIENCE! ") == "computer science"
    assert normalize("C++") == "c++"
    assert SearchIndex(Vocabulary(documents=[], universities=[])).suggestions("") == []
