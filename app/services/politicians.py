"""MPs from the Sejm API (api.sejm.gov.pl): name search + the face index in Qdrant built from their photos."""
import logging
import threading
import time
import unicodedata
from collections import Counter
from functools import lru_cache

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.faces import EMBEDDING_DIM

logger = logging.getLogger(__name__)

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
    district_number: int | None = Field(None, description="Electoral district number (okręg nr).")
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
            district_number=mp.get("districtNum"),
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


class PoliticianListItem(BaseModel):
    """Row of the "Szukaj posła" list."""

    mp_id: int
    first_name: str
    last_name: str
    full_name: str
    letter: str = Field(description="Section header: first letter of the last name, uppercased (`Ł` is its own section).")
    club: str | None = Field(description="Current club id, e.g. `KO`; filter value for `club`.")
    club_label: str | None = Field(description="Club name to display, e.g. `Rozwój Plus` for `RozwojPlus`.")
    district_number: int | None = Field(description="Electoral district number (okręg nr).")
    district_name: str | None = Field(description="Electoral district (okręg), e.g. `Kraków`.")
    active: bool
    photo_url: str


class Club(BaseModel):
    """Parliamentary club or circle (klub/koło) as published by the Sejm API."""

    id: str
    name: str


class ClubOut(BaseModel):
    id: str = Field(description="Club id; pass as `club` to `GET /api/v1/politicians`.")
    label: str = Field(description="Short name for the chip.")
    name: str = Field(description="Full official name.")
    members_count: int = Field(description="MPs listed by `GET /api/v1/politicians?club={id}` with the same `include_inactive`.")


# Sejm club ids are not display names; unknown ids fall back to `id.replace("_", " ")`.
CLUB_LABELS = {
    "Polska2050": "Polska 2050",
    "Polska2050-TD": "Polska 2050-TD",
    "RozwojPlus": "Rozwój Plus",
    "Konfederacja_KP": "Konfederacja KP",
    "Demokracja": "Demokracja Bezpośrednia",
    "niez.": "Niezrzeszeni",
}
NO_CLUB = "niez."


def club_label(club_id: str | None) -> str | None:
    if club_id is None:
        return None
    return CLUB_LABELS.get(club_id, club_id.replace("_", " "))


class _Cached:
    """Value fetched from the Sejm API, refreshed after MP_LIST_TTL_SECONDS.

    A failed refresh keeps serving the stale value; it raises only when nothing was fetched yet.
    """

    def __init__(self, fetch):
        self._fetch = fetch
        self._lock = threading.Lock()
        self._value = None
        self._fetched_at = 0.0

    def get(self):
        with self._lock:
            if self._value is None or time.monotonic() - self._fetched_at > MP_LIST_TTL_SECONDS:
                try:
                    self._value = self._fetch()
                except httpx.HTTPError:
                    if self._value is None:
                        raise
                    logger.warning("Sejm API refresh failed, serving cached data", exc_info=True)
                self._fetched_at = time.monotonic()
            return self._value


def _fetch_mps() -> list[Politician]:
    term = get_settings().sejm_term
    r = httpx.get(f"{SEJM_API}/term{term}/MP", timeout=30)
    r.raise_for_status()
    return [Politician.from_sejm(mp, term) for mp in r.json()]


def _fetch_clubs() -> list[Club]:
    r = httpx.get(f"{SEJM_API}/term{get_settings().sejm_term}/clubs", timeout=30)
    r.raise_for_status()
    return [Club(id=c["id"], name=c.get("name") or c["id"]) for c in r.json()]


_mps = _Cached(_fetch_mps)
_clubs = _Cached(_fetch_clubs)


def list_politicians() -> list[Politician]:
    """All MPs of the current term (including expired mandates), cached in memory."""
    return _mps.get()


def list_clubs() -> list[Club]:
    """Clubs of the current term, cached in memory."""
    return _clubs.get()


def get_politician(mp_id: int) -> Politician | None:
    return next((p for p in list_politicians() if p.mp_id == mp_id), None)


def _fold(text: str) -> str:
    # "Łódź" -> "lodz", so users can type without Polish characters. "ł" has no Unicode decomposition.
    text = unicodedata.normalize("NFKD", text.casefold().replace("ł", "l"))
    return "".join(c for c in text if not unicodedata.combining(c))


def search_politicians(query: str, limit: int) -> list[Politician]:
    """Every word of the query must start a word of the MP's name; last-name hits first, then alphabetically."""
    words = _fold(query).split()
    hits = [p for p in list_politicians() if words and _matches(p, words)]
    hits.sort(key=lambda p: (not _fold(p.last_name).startswith(words[0]), _fold(p.last_name), _fold(p.first_name)))
    return hits[:limit]


def _matches(p: Politician, words: list[str]) -> bool:
    """Every query word is a prefix of some word of the name."""
    name_words = _fold(p.full_name).split()
    return all(any(n.startswith(w) for n in name_words) for w in words)


_POLISH_ALPHABET = "aąbcćdeęfghijklłmnńoópqrsśtuvwxyzźż"
_POLISH_RANK = {c: i for i, c in enumerate(_POLISH_ALPHABET)}


def polish_sort_key(text: str) -> tuple[int, ...]:
    """Case-insensitive Polish alphabetical order: `Lis` < `Łęcki` < `Maj`.

    Letters outside the Polish alphabet (e.g. `ü`) sort as their base letter; spaces and hyphens before letters.
    """
    key = []
    for c in text.casefold():
        rank = _POLISH_RANK.get(c)
        if rank is None:
            base = _fold(c)
            rank = _POLISH_RANK.get(base[:1], -1) if base else -1
        key.append(rank)
    return tuple(key)


def _list_key(p: Politician) -> tuple:
    return polish_sort_key(p.last_name), polish_sort_key(p.first_name), p.mp_id


def _visible(include_inactive: bool) -> list[Politician]:
    return [p for p in list_politicians() if include_inactive or p.active]


def filter_politicians(query: str | None, club: str | None, include_inactive: bool) -> list[Politician]:
    """MPs for the "Szukaj posła" list: name prefix match + exact current club, alphabetical by last name."""
    words = _fold(query or "").split()
    hits = [
        p
        for p in _visible(include_inactive)
        if (club is None or p.club == club) and (not words or _matches(p, words))
    ]
    return sorted(hits, key=_list_key)


def to_list_item(p: Politician) -> PoliticianListItem:
    return PoliticianListItem(
        mp_id=p.mp_id,
        first_name=p.first_name,
        last_name=p.last_name,
        full_name=p.full_name,
        letter=p.last_name[:1].upper(),
        club=p.club,
        club_label=club_label(p.club),
        district_number=p.district_number,
        district_name=p.district,
        active=p.active,
        photo_url=p.photo_url,
    )


def clubs_with_counts(include_inactive: bool) -> list[ClubOut]:
    """Clubs that have at least one listed MP; biggest first, unaffiliated MPs last."""
    counts = Counter(p.club for p in _visible(include_inactive) if p.club)
    names = {c.id: c.name for c in list_clubs()}
    clubs = [
        ClubOut(id=cid, label=club_label(cid), name=names.get(cid, club_label(cid)), members_count=n)
        for cid, n in counts.items()
    ]
    return sorted(clubs, key=lambda c: (c.id == NO_CLUB, -c.members_count, polish_sort_key(c.label)))


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
