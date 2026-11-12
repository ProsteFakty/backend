"""MPs from the Sejm API (api.sejm.gov.pl): name search + the face index in Qdrant built from their photos."""
import threading
import time
import unicodedata
from functools import lru_cache

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.faces import EMBEDDING_DIM

SEJM_API = "https://api.sejm.gov.pl/sejm"
COLLECTION = "politician_faces"
# The MP list barely changes; refetching it per search would make us depend on a slow API for every keystroke.
MP_LIST_TTL_SECONDS = 6 * 3600


class Politician(BaseModel):
    """MP data as published by the Sejm API."""

    mp_id: int = Field(description="MP id in the Sejm API, unique within a term.")
    term: int = Field(description="Sejm term (kadencja).")
    first_name: str
    last_name: str
    full_name: str
    club: str | None = Field(None, description="Parliamentary club (klub/koło), e.g. `KO`, `PiS`.")
    district: str | None = Field(None, description="Electoral district (okręg wyborczy).")
    voivodeship: str | None = None
    birth_date: str | None = None
    birth_location: str | None = None
    profession: str | None = None
    education: str | None = None
    email: str | None = None
    number_of_votes: int | None = Field(None, description="Votes received in the election.")
    active: bool = Field(description="False if the mandate expired during the term.")
    photo_url: str = Field(description="Official photo, served by the Sejm API.")
    profile_url: str = Field(description="MP profile on sejm.gov.pl.")

    @classmethod
    def from_sejm(cls, mp: dict, term: int) -> "Politician":
        return cls(
            mp_id=mp["id"],
            term=term,
            first_name=mp["firstName"],
            last_name=mp["lastName"],
            full_name=mp["firstLastName"],
            club=mp.get("club"),
            district=mp.get("districtName"),
            voivodeship=mp.get("voivodeship"),
            birth_date=mp.get("birthDate"),
            birth_location=mp.get("birthLocation"),
            profession=mp.get("profession"),
            education=mp.get("educationLevel"),
            email=mp.get("email"),
            number_of_votes=mp.get("numberOfVotes"),
            active=mp.get("active", False),
            photo_url=f"{SEJM_API}/term{term}/MP/{mp['id']}/photo",
            profile_url=f"https://www.sejm.gov.pl/Sejm{term}.nsf/posel.xsp?id={mp['id']:03d}&type=A",
        )

    @property
    def point_id(self) -> int:
        # Unique across terms, so another term can be indexed into the same collection later.
        return self.term * 1000 + self.mp_id


class PoliticianSummary(BaseModel):
    """Search result row; the full profile is GET /api/v1/politicians/{mp_id}."""

    mp_id: int
    full_name: str
    club: str | None
    active: bool
    photo_url: str


class Match(BaseModel):
    politician: Politician
    similarity: float


_mps_lock = threading.Lock()
_mps: list[Politician] = []
_mps_fetched_at = 0.0


def list_politicians() -> list[Politician]:
    """All MPs of the current term (including expired mandates), cached in memory."""
    global _mps, _mps_fetched_at
    with _mps_lock:
        if not _mps or time.monotonic() - _mps_fetched_at > MP_LIST_TTL_SECONDS:
            term = get_settings().sejm_term
            r = httpx.get(f"{SEJM_API}/term{term}/MP", timeout=30)
            r.raise_for_status()
            _mps = [Politician.from_sejm(mp, term) for mp in r.json()]
            _mps_fetched_at = time.monotonic()
        return _mps


def get_politician(mp_id: int) -> Politician | None:
    return next((p for p in list_politicians() if p.mp_id == mp_id), None)


def _fold(text: str) -> str:
    # "Łódź" -> "lodz", so users can type without Polish characters. "ł" has no Unicode decomposition.
    text = unicodedata.normalize("NFKD", text.casefold().replace("ł", "l"))
    return "".join(c for c in text if not unicodedata.combining(c))


def search_politicians(query: str, limit: int) -> list[Politician]:
    """Every word of the query must start a word of the MP's name; last-name hits first, then alphabetically."""
    words = _fold(query).split()
    hits = []
    for p in list_politicians():
        name_words = _fold(p.full_name).split()
        if words and all(any(n.startswith(w) for n in name_words) for w in words):
            hits.append(p)
    hits.sort(key=lambda p: (not _fold(p.last_name).startswith(words[0]), _fold(p.last_name), _fold(p.first_name)))
    return hits[:limit]


@lru_cache
def _qdrant() -> httpx.Client:
    return httpx.Client(base_url=get_settings().qdrant_url, timeout=10)


def ensure_collection() -> None:
    if _qdrant().get(f"/collections/{COLLECTION}").status_code == 404:
        _qdrant().put(
            f"/collections/{COLLECTION}",
            json={"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}},
        ).raise_for_status()


def upsert_faces(items: list[tuple[Politician, list[float]]]) -> None:
    points = [{"id": p.point_id, "vector": v, "payload": p.model_dump()} for p, v in items]
    _qdrant().put(
        f"/collections/{COLLECTION}/points", params={"wait": "true"}, json={"points": points}
    ).raise_for_status()


def search_faces(embedding: list[float], limit: int = 1) -> list[Match]:
    r = _qdrant().post(
        f"/collections/{COLLECTION}/points/query",
        json={"query": embedding, "limit": limit, "with_payload": True},
    )
    if r.status_code == 404:  # index not built yet
        return []
    r.raise_for_status()
    return [
        Match(politician=Politician(**p["payload"]), similarity=p["score"])
        for p in r.json()["result"]["points"]
    ]
