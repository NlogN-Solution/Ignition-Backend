"""Published catalogue search vocabulary and deterministic intent matching.

Only this module owns aliases and popular intents. The browser receives small
suggestion DTOs; the same matcher narrows the existing course query/facets.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import lru_cache
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.cache import get_json, set_json
from ..core.public_cache import TTL_SECONDS, _enabled, cache_key
from ..models import Program, University

POPULAR_SEARCHES = ("Computer Science", "Business", "Engineering", "MSc", "Artificial Intelligence", "Nursing")
ALIASES: dict[str, tuple[str, ...]] = {
    "comp sci": ("computer science",),
    "cs": ("computer science",),
    "ai": ("artificial intelligence", "machine learning"),
    "it": ("information technology", "computing"),
    "software": ("software engineering", "software development", "computer science"),
    "business": ("business management", "management"),
    "masters": ("postgraduate",),
    "master": ("postgraduate",),
}
RELATED: dict[str, tuple[str, ...]] = {
    "computer": ("computing", "computer engineering", "artificial intelligence", "data science"),
    "computer science": ("computing", "computer engineering", "artificial intelligence", "data science"),
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w+#]+", " ", value).split())


@lru_cache(maxsize=32768)
def token_similarity(query: str, token: str) -> float:
    if query == token:
        return 1.0
    if len(query) >= 2 and query not in {"msc", "ma", "mba", "mres", "bsc", "ba", "phd"} and token.startswith(query):
        return 0.94
    if min(len(query), len(token)) < 4:
        return 0.0
    budget = 1 if len(query) <= 5 else 2
    if abs(len(query) - len(token)) > budget:
        return 0.0
    previous = list(range(len(token) + 1))
    for i, left in enumerate(query, 1):
        current = [i]
        for j, right in enumerate(token, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        if min(current) > budget:
            return 0.0
        previous = current
    distance = previous[-1]
    similarity = 1 - distance / max(len(query), len(token))
    return similarity if distance <= budget and similarity >= 0.72 else 0.0


def lexical_score(query: str, value: str) -> float:
    if query == value:
        return 600.0
    if len(query) >= 2 and value.startswith(query) and query not in {"msc", "ma", "mba", "mres", "bsc", "ba", "phd"}:
        return 500.0
    tokens = value.split()
    query_tokens = query.split()
    if not tokens or not query_tokens:
        return 0.0
    matches = [max((token_similarity(q, t) for t in tokens), default=0) for q in query_tokens]
    if not all(matches):
        return 0.0
    if all(score == 1 for score in matches):
        return 400.0
    if all(score >= 0.94 for score in matches):
        return 350.0
    return 250.0 + sum(matches) / len(matches)


def expansions(query: str) -> tuple[str, ...]:
    alternatives = list(ALIASES.get(query, ()))
    for alias, replacements in ALIASES.items():
        if alias != query and re.search(rf"\b{re.escape(alias)}\b", query):
            alternatives.extend(re.sub(rf"\b{re.escape(alias)}\b", replacement, query) for replacement in replacements)
    return tuple(alternatives)


class SearchDestination(BaseModel):
    path: Literal["/courses", "/universities"] = "/courses"
    q: str
    university: str | None = None
    qualification: str | None = None
    location: str | None = None
    route: str | None = None
    level: str | None = None
    subject: str | None = None


class SearchSuggestion(BaseModel):
    label: str
    value: str
    type: Literal["subject", "course", "university", "location", "degree", "keyword"]
    destination: SearchDestination
    result_count: int | None = None
    exact_entity: bool = False
    entity_slug: str | None = None


class SearchSuggestions(BaseModel):
    items: list[SearchSuggestion]


class SearchDocument(BaseModel):
    id: str
    title: str
    qualification: str = ""
    subject: str = ""
    level: str = ""
    university: str
    university_name: str
    city: str = ""
    campus: str = ""


class SearchUniversity(BaseModel):
    slug: str
    name: str
    city: str = ""


class Vocabulary(BaseModel):
    documents: list[SearchDocument]
    universities: list[SearchUniversity]


class Concept(BaseModel):
    label: str
    type: Literal["subject", "course", "university", "location", "degree", "keyword"]
    destination: SearchDestination
    ids: set[str] = Field(default_factory=set)


def title_concept(title: str, qualification: str) -> str:
    value = title.strip()
    if qualification:
        value = re.sub(rf"^\s*{re.escape(qualification)}\s+|\s+{re.escape(qualification)}\s*$", "", value, flags=re.I)
    value = re.sub(r"^(?:BA|BSc|BEng|MSc|MA|MBA|MRes|LLM|LLB|PhD)(?:\s*\(Hons\))?\s+", "", value, flags=re.I)
    value = re.sub(
        r"\s+(?:with (?:a )?(?:foundation|placement) year|with year in industry|with professional placement).*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s*\((?:\d+ years?|level \d+ direct entry)\)\s*$", "", value, flags=re.I)
    return " ".join(value.split()).strip(" -")


class SearchIndex:
    def __init__(self, vocabulary: Vocabulary):
        self.vocabulary = vocabulary
        self.concepts: dict[str, Concept] = {}
        self.texts: dict[str, str] = {}
        for doc in vocabulary.documents:
            self.texts[doc.id] = normalize(
                " ".join(
                    (doc.title, doc.qualification, doc.subject, doc.level, doc.university_name, doc.city, doc.campus)
                )
            )
            base = title_concept(doc.title, doc.qualification)
            self.add(base, "course", doc.id)
            if base and doc.qualification in {"MSc", "MA", "MRes", "LLM", "MBA"}:
                self.add(f"{base} {doc.qualification}", "course", doc.id, qualification=doc.qualification)
            self.add(doc.subject, "subject", doc.id, subject=doc.subject)
            self.add(doc.qualification, "degree", doc.id, qualification=doc.qualification)
            self.add(doc.level, "degree", doc.id, level=doc.level)
            self.add(doc.university_name, "university", doc.id, university=doc.university)
            for city in locations(doc.city, doc.campus):
                self.add(city, "location", doc.id, location=city)

    def add(
        self,
        label: str,
        kind: Literal["subject", "course", "university", "location", "degree", "keyword"],
        doc_id: str,
        **filters: str,
    ):
        key = normalize(label)
        if not key or len(key) < 2:
            return
        if key not in self.concepts:
            self.concepts[key] = Concept(label=label, type=kind, destination=SearchDestination(q=label, **filters))
        self.concepts[key].ids.add(doc_id)

    def matches(self, query: str) -> dict[str, float]:
        query = normalize(query[:100])
        aliases = expansions(query)
        if query in {"ai", "cs", "it", "comp sci"}:
            query = ALIASES[query][0]
        result = {}
        for doc_id, text in self.texts.items():
            score = lexical_score(query, text)
            if not score:
                score = max((min(200.0, lexical_score(alias, text)) for alias in aliases), default=0)
            if score:
                result[doc_id] = score
        return result

    def suggestions(self, query: str) -> list[SearchSuggestion]:
        query = normalize(query[:100])
        if not query:
            return [
                self.as_suggestion(self.concepts[normalize(term)])
                for term in POPULAR_SEARCHES
                if normalize(term) in self.concepts
            ]
        if len(query) < 2:
            return []
        exact = [u for u in self.vocabulary.universities if normalize(u.name) == query]
        if len(exact) == 1:
            uni = exact[0]
            items = [
                SearchSuggestion(
                    label=uni.name,
                    value=query,
                    type="university",
                    exact_entity=True,
                    entity_slug=uni.slug,
                    destination=SearchDestination(path="/universities", q=uni.name),
                )
            ]
            docs = [d for d in self.vocabulary.documents if d.university == uni.slug]
            if docs:
                items.append(
                    SearchSuggestion(
                        label=f"Courses at {uni.name}",
                        value=query,
                        type="keyword",
                        destination=SearchDestination(q=uni.name, university=uni.slug),
                    )
                )
            if any(d.level == "Postgraduate" for d in docs):
                items.append(
                    SearchSuggestion(
                        label=f"Masters at {uni.name}",
                        value=query,
                        type="keyword",
                        destination=SearchDestination(q=uni.name, university=uni.slug, route="postgraduate"),
                    )
                )
            subjects = Counter(title_concept(d.title, d.qualification) for d in docs)
            for label, _ in subjects.most_common(3):
                items.append(
                    SearchSuggestion(
                        label=f"{label} at {uni.name}",
                        value=normalize(label),
                        type="course",
                        destination=SearchDestination(q=label, university=uni.slug),
                    )
                )
            return items[:6]
        aliases = expansions(query)
        if query in {"ai", "cs", "it", "comp sci"}:
            query = ALIASES[query][0]
        related = RELATED.get(query, ())
        ranked: list[tuple[float, int, str, Concept]] = []
        for key, concept in self.concepts.items():
            score = lexical_score(query, key)
            if not score:
                score = max((min(200.0, lexical_score(alias, key)) for alias in aliases), default=0)
            if not score and key in related:
                score = 100.0
            if score:
                # Prefer concise base concepts over decorated one-off titles.
                ranked.append((score, len(concept.ids), key, concept))
        ranked.sort(key=lambda item: (-item[0], -item[1], len(item[2]), item[2]))
        chosen: list[SearchSuggestion] = []
        combinations = 0
        for _, _, _, concept in ranked:
            if concept.type == "course" and concept.destination.qualification:
                if combinations:
                    continue
                combinations += 1
            chosen.append(self.as_suggestion(concept))
            if len(chosen) == 6:
                break
        return chosen

    @staticmethod
    def as_suggestion(concept: Concept) -> SearchSuggestion:
        return SearchSuggestion(
            label=concept.label, value=normalize(concept.label), type=concept.type, destination=concept.destination
        )


def locations(city: str, campus: str) -> list[str]:
    return list(dict.fromkeys(value.strip().title() for value in [city, *campus.split("/")] if value.strip()))


_cached_index: tuple[str, SearchIndex] | None = None


async def search_index(session: AsyncSession) -> SearchIndex:
    global _cached_index
    key = cache_key("search-vocabulary-v2", "")
    cached = await get_json(key) if _enabled() else None
    if cached is not None:
        version = cached["version"]
        if _cached_index is not None and _cached_index[0] == version:
            return _cached_index[1]
        index = SearchIndex(Vocabulary.model_validate(cached["vocabulary"]))
        _cached_index = (version, index)
        return index
    rows = await session.execute(
        select(
            Program.id,
            Program.name,
            Program.qualification,
            Program.subject,
            Program.course_level,
            University.slug,
            University.name,
            University.city,
            Program.campus,
        )
        .join(University, Program.university_id == University.id)
        .where(
            Program.is_published.is_(True),
            University.is_published.is_(True),
            Program.is_example.is_(False),
            University.is_example.is_(False),
        )
    )
    documents = [
        SearchDocument(
            id=str(row[0]),
            title=row[1],
            qualification=row[2] or "",
            subject=row[3].value if row[3] else "",
            level=row[4].value if row[4] else "",
            university=row[5],
            university_name=row[6],
            city=row[7] or "",
            campus=row[8] or "",
        )
        for row in rows
        if row[5]
    ]
    unis = await session.execute(
        select(University.slug, University.name, University.city).where(
            University.is_published.is_(True), University.is_example.is_(False)
        )
    )
    vocabulary = Vocabulary(
        documents=documents,
        universities=[SearchUniversity(slug=r[0], name=r[1], city=r[2] or "") for r in unis if r[0]],
    )
    index = SearchIndex(vocabulary)
    if _enabled():
        version = str(uuid4())
        await set_json(key, {"version": version, "vocabulary": vocabulary.model_dump(mode="json")}, TTL_SECONDS)
        _cached_index = (version, index)
    return index


def matched_ids(matches: dict[str, float]) -> list[UUID]:
    return [UUID(value) for value in matches]
