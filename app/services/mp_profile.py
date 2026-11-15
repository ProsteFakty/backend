"""Extra data for the MP profile screen: functions, terms, voting stats (docs/specs/mp-profile.md).

Everything here is best effort: a failed Sejm call leaves its fields empty instead of failing the profile.
"""
import logging
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services import politicians
from app.services.politicians import SEJM_API, Politician, _Cached, club_label

logger = logging.getLogger(__name__)

# api.sejm.gov.pl returns empty MP lists for terms 1-2.
FIRST_TERM_IN_API = 3
NEVER = math.inf  # closed terms do not change

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sejm")


class MpProfile(Politician):
    """`Politician` + data for the profile screen."""

    gender: Literal["female", "male"] = Field(
        description="Guessed from the first name (Sejm API has no gender). FE: Posłanka / Poseł."
    )
    club_label: str | None = Field(description="Short club label, e.g. `KO`, `Rozwój Plus`.")
    club_name: str | None = Field(description="Club name for the profile chip, e.g. `Koalicja Obywatelska`.")
    functions: list[str] = Field(
        description="Committee and club functions, most important first, e.g. `Przewodnicząca Komisji Zdrowia`."
    )
    terms: list[int] = Field(description="Sejm terms as an MP (from term 3), ascending, incl. the current one.")
    attendance_percent: float | None = Field(
        description="Obecność (\"97%\" tile). Current term: `votes_cast / votings_count * 100`, 0-100, 1 decimal. "
        "Abstaining counts as present; excused and unexcused absences both count as missed. "
        "Null if the Sejm API stats are unavailable or no votings were held yet during the mandate.",
        examples=[98.4],
    )
    votes_cast: int | None = Field(
        description="Current term: number of votings the MP took part in (voted for, against or abstained). "
        "Sum of `numVoted` from Sejm `GET /term{t}/MP/{id}/votings/stats`. "
        "`votings_count - votes_cast` = votings missed. Null if the stats are unavailable.",
        examples=[5147],
    )
    votings_count: int | None = Field(
        description="Current term: number of votings held while the MP had the mandate (an MP who joined mid-term "
        "has fewer). Sum of `numVotings` from the same Sejm stats. Null if the stats are unavailable.",
        examples=[5232],
    )
    votes_cast_all_terms: int | None = Field(
        description="Same as `votes_cast`, summed over every term in `terms` (each with that term's MP id). "
        "Null if any term's stats or MP list could not be loaded, so a partial sum is never returned.",
        examples=[28174],
    )
    office_address: str | None = Field(None, description="Biuro poselskie. Always null: no source yet.")


# --- gender, club name -------------------------------------------------------------------------------------


def gender(first_name: str) -> Literal["female", "male"]:
    # Polish female first names end with "a"; no exception among term 10 MPs.
    return "female" if first_name.strip().lower().endswith("a") else "male"


_CLUB_NAME_PREFIXES = ("Koalicyjny Klub Parlamentarny ", "Klub Parlamentarny ", "Klub Poselski ", "Koło Poselskie ")
_CLUB_NAME_OVERRIDES = {"Lewica": "Lewica", "niez.": "Posłowie niezrzeszeni"}


def club_name(club_id: str | None, official_name: str | None) -> str | None:
    """`Klub Parlamentarny Koalicja Obywatelska - Platforma …` -> `Koalicja Obywatelska`."""
    if club_id is None:
        return None
    if club_id in _CLUB_NAME_OVERRIDES:
        return _CLUB_NAME_OVERRIDES[club_id]
    if not official_name:
        return club_label(club_id)
    name = official_name
    for prefix in _CLUB_NAME_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    name = re.split(r" - | \(", name, maxsplit=1)[0].strip()
    return name or club_label(club_id)


# --- functions ---------------------------------------------------------------------------------------------


class Committee(BaseModel):
    name_genitive: str  # "Komisji Zdrowia"
    member_functions: dict[int, str]


def _fetch_committees() -> list[Committee]:
    r = httpx.get(f"{SEJM_API}/term{get_settings().sejm_term}/committees", timeout=30)
    r.raise_for_status()
    return [
        Committee(
            name_genitive=c.get("nameGenitive") or c["name"],
            member_functions={m["id"]: m["function"] for m in c.get("members", []) if m.get("function")},
        )
        for c in r.json()
    ]


_committees = _Cached(_fetch_committees)


def list_committees() -> list[Committee]:
    return _committees.get()


def _feminize(function: str) -> str:
    # The Sejm API often uses the masculine form for women ("przewodniczący"); "zastępca przewodniczącego" refers
    # to the (male) chair, so only the MP's own title is changed.
    return re.sub(r"\b(wice)?przewodniczący\b", r"\1przewodnicząca", function)


def _rank(function: str) -> int:
    f = function.lower()
    if f.startswith("przewodnicząc"):
        return 0
    if "wiceprzewodnicz" in f or f.startswith("zastęp"):
        return 1
    return 2


def functions(mp: Politician, sex: str) -> list[str]:
    found: list[tuple[int, int, str]] = []  # (rank, source: committee first, text)
    try:
        for c in list_committees():
            if fn := c.member_functions.get(mp.mp_id):
                found.append((_rank(fn), 0, f"{fn} {c.name_genitive}"))
    except httpx.HTTPError:
        logger.warning("Sejm committees unavailable", exc_info=True)
    try:
        for club in politicians.list_clubs():
            if club.id == mp.club and (fn := club.member_functions.get(mp.mp_id)):
                found.append((_rank(fn), 1, f"{fn} klubu {club_label(club.id)}"))
    except httpx.HTTPError:
        logger.warning("Sejm clubs unavailable", exc_info=True)
    found.sort(key=lambda f: (f[0], f[1]))
    texts = [_feminize(text) if sex == "female" else text for _, _, text in found]
    return [t[:1].upper() + t[1:] for t in texts]


# --- terms -------------------------------------------------------------------------------------------------

PersonKey = tuple[str, str, str | None]


def _person_key(first_name: str, last_name: str, birth_date: str | None) -> PersonKey:
    return first_name.strip().casefold(), last_name.strip().casefold(), birth_date


def _fetch_term_ids(term: int) -> dict[PersonKey, int]:
    r = httpx.get(f"{SEJM_API}/term{term}/MP", timeout=30)
    r.raise_for_status()
    return {_person_key(m["firstName"], m["lastName"], m.get("birthDate")): m["id"] for m in r.json()}


_past_terms: dict[int, _Cached] = {}
_past_terms_lock = threading.Lock()


def _term_ids(term: int) -> dict[PersonKey, int]:
    with _past_terms_lock:
        cached = _past_terms.setdefault(term, _Cached(lambda: _fetch_term_ids(term), ttl=NEVER))
    return cached.get()


def terms(mp: Politician) -> tuple[dict[int, int], bool]:
    """Term -> the person's MP id in that term, and whether every past term could be checked.

    Matched by first name + last name + birth date; terms 1-2 are not in the Sejm API.
    """
    key = _person_key(mp.first_name, mp.last_name, mp.birth_date)
    found = {mp.term: mp.mp_id}
    complete = True

    def lookup(term: int) -> int | None:
        return _term_ids(term).get(key)

    past = range(FIRST_TERM_IN_API, mp.term)
    for term, future in [(t, _pool.submit(lookup, t)) for t in past]:
        try:
            if (mp_id := future.result()) is not None:
                found[term] = mp_id
        except httpx.HTTPError:
            complete = False
            logger.warning("Sejm MP list of term %s unavailable", term, exc_info=True)
    return dict(sorted(found.items())), complete


# --- voting stats ------------------------------------------------------------------------------------------


class VotingStats(BaseModel):
    voted: int
    votings: int


_stats: dict[tuple[int, int], _Cached] = {}
_stats_lock = threading.Lock()


def _fetch_stats(term: int, mp_id: int) -> VotingStats:
    r = httpx.get(f"{SEJM_API}/term{term}/MP/{mp_id}/votings/stats", timeout=30)
    r.raise_for_status()
    days = r.json()
    return VotingStats(voted=sum(d.get("numVoted", 0) for d in days), votings=sum(d.get("numVotings", 0) for d in days))


def voting_stats(term: int, mp_id: int) -> VotingStats:
    ttl = None if term == get_settings().sejm_term else NEVER
    with _stats_lock:
        cached = _stats.setdefault((term, mp_id), _Cached(lambda: _fetch_stats(term, mp_id), ttl=ttl))
    return cached.get()


def attendance_percent(stats: VotingStats) -> float | None:
    return round(stats.voted / stats.votings * 100, 1) if stats.votings else None


# --- profile -----------------------------------------------------------------------------------------------


def build_profile(mp: Politician) -> MpProfile:
    sex = gender(mp.first_name)
    functions_future = _pool.submit(functions, mp, sex)
    term_ids, terms_complete = terms(mp)

    stats: dict[int, VotingStats | None] = {}
    for term, future in [(t, _pool.submit(voting_stats, t, i)) for t, i in term_ids.items()]:
        try:
            stats[term] = future.result()
        except httpx.HTTPError:
            stats[term] = None
            logger.warning("Sejm voting stats unavailable for term %s", term, exc_info=True)

    current = stats.get(mp.term)
    all_known = terms_complete and all(s is not None for s in stats.values())

    official = None
    try:
        official = next((c.name for c in politicians.list_clubs() if c.id == mp.club), None)
    except httpx.HTTPError:
        logger.warning("Sejm clubs unavailable", exc_info=True)

    return MpProfile(
        **mp.model_dump(),
        gender=sex,
        club_label=club_label(mp.club),
        club_name=club_name(mp.club, official),
        functions=functions_future.result(),
        terms=list(term_ids),
        attendance_percent=attendance_percent(current) if current else None,
        votes_cast=current.voted if current else None,
        votings_count=current.votings if current else None,
        votes_cast_all_terms=sum(s.voted for s in stats.values()) if all_known else None,
    )
