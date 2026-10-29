"""Politician face index in Qdrant, built from MP photos in the Sejm API (api.sejm.gov.pl)."""
from functools import lru_cache

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.faces import EMBEDDING_DIM

SEJM_API = "https://api.sejm.gov.pl/sejm"
COLLECTION = "politician_faces"


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


class Match(BaseModel):
    politician: Politician
    similarity: float


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
