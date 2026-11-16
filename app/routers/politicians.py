import math

import httpx
from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas.sittings import Page
from app.services.faces import decode_image, get_engine
from app.services.mp_profile import MpProfile, build_profile
from app.services.politicians import (
    ClubOut,
    Politician,
    PoliticianListItem,
    PoliticianSummary,
    clubs_with_counts,
    filter_politicians,
    get_photo,
    get_politician,
    search_faces,
    search_politicians,
    to_list_item,
)

router = APIRouter(prefix="/api/v1/politicians", tags=["politicians"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class RecognizedFace(BaseModel):
    box: tuple[int, int, int, int] = Field(
        description="Face bounding box `[x, y, width, height]` in pixels of the uploaded photo."
    )
    politician: Politician | None = Field(
        description="Best matching MP, or `null` when a face was found but nobody in the index is similar enough."
    )
    similarity: float | None = Field(
        description="Cosine similarity to the best candidate (higher = more alike). "
        "A match needs at least `FACE_MATCH_THRESHOLD` (default 0.363). `null` when the index is empty."
    )


class RecognizeResponse(BaseModel):
    faces: list[RecognizedFace] = Field(description="Detected faces, largest first. Empty = no face in the photo.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "faces": [
                        {
                            "box": [412, 230, 318, 402],
                            "similarity": 0.61,
                            "politician": {
                                "mp_id": 1,
                                "term": 10,
                                "first_name": "Andrzej",
                                "last_name": "Adamczyk",
                                "full_name": "Andrzej Adamczyk",
                                "club": "PiS",
                                "district": "Kraków",
                                "district_number": 13,
                                "voivodeship": "małopolskie",
                                "birth_date": "1959-01-04",
                                "birth_location": "Krzeszowice",
                                "profession": "ekonomista",
                                "education": "wyższe",
                                "email": "Andrzej.Adamczyk@sejm.pl",
                                "number_of_votes": 45171,
                                "active": True,
                                "photo_url": "http://localhost:8000/api/v1/politicians/1/photo",
                                "profile_url": "https://www.sejm.gov.pl/Sejm10.nsf/posel.xsp?id=001&type=A",
                            },
                        },
                        {"box": [40, 310, 120, 150], "similarity": 0.18, "politician": None},
                    ]
                }
            ]
        }
    }


class ErrorResponse(BaseModel):
    detail: str


SEJM_DOWN = {502: {"model": ErrorResponse, "description": "Sejm API unavailable and no MP data cached yet."}}
INCLUDE_INACTIVE = Query(False, description="Also list MPs whose mandate expired during the term.")


def _with_photo_url(request: Request, politician: Politician) -> Politician:
    """Absolute URL of our photo proxy, whatever is stored (old face-index entries still hold Sejm API URLs).

    Every endpoint that returns an MP goes through this: Android does not trust the certificate of
    api.sejm.gov.pl, so the app must never be handed a photo URL on that host.
    """
    url = str(request.url_for("politician_photo", mp_id=politician.mp_id))
    return politician.model_copy(update={"photo_url": url})


@router.get("", summary="List MPs (Szukaj posła)", responses=SEJM_DOWN)
def list_mps(
    request: Request,
    q: str | None = Query(
        None, max_length=100, description="Name fragment(s), e.g. `a`, `kowal jan`. Polish characters optional."
    ),
    club: str | None = Query(None, description="Club id from `GET /api/v1/politicians/clubs`, e.g. `KO`. empty club returns all and makes it almost like /search endpoint"),
    include_inactive: bool = INCLUDE_INACTIVE,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
) -> Page[PoliticianListItem]:
    """Matching MPs of the current term, alphabetical by last name in Polish order, paginated.

    Group rows into sections by `letter` (a section can continue on the next page).
    An unknown `club` or a page past the end returns empty `items`.
    """
    try:
        mps = filter_politicians(q, club, include_inactive)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Sejm API is unavailable") from exc
    start = (page - 1) * page_size
    return Page(
        items=[to_list_item(_with_photo_url(request, p)) for p in mps[start : start + page_size]],
        page=page,
        page_size=page_size,
        total=len(mps),
        pages=math.ceil(len(mps) / page_size),
    )


# Registered before /{mp_id}, otherwise "clubs" would be parsed as an MP id.
@router.get("/clubs", summary="Club filter chips", responses=SEJM_DOWN)
def clubs(include_inactive: bool = INCLUDE_INACTIVE) -> list[ClubOut]:
    """Clubs with at least one MP, biggest first, `niez.` (unaffiliated) last.

    `members_count` equals `total` of `GET /api/v1/politicians?club={id}` with the same `include_inactive`.
    """
    try:
        return clubs_with_counts(include_inactive)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Sejm API is unavailable") from exc


@router.get("/search", summary="Search MPs by name", responses=SEJM_DOWN)
def search(
    request: Request,
    q: str = Query(min_length=2, description="Name fragment(s), e.g. `adam`, `kowal jan`. Polish characters optional."),
    limit: int = Query(20, ge=1, le=100),
) -> list[PoliticianSummary]:
    """Matches word prefixes of first and last name; MPs whose last name starts with the query come first.

    Open a result with `GET /api/v1/politicians/{mp_id}`.
    """
    try:
        politicians = search_politicians(q, limit)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Sejm API is unavailable") from exc
    return [PoliticianSummary(**_with_photo_url(request, p).model_dump()) for p in politicians]


@router.get(
    "/{mp_id}",
    summary="MP profile",
    responses={404: {"model": ErrorResponse, "description": "No MP with this id in the current term."}, **SEJM_DOWN},
)
def profile(request: Request, mp_id: int) -> MpProfile:
    """Data for the "Profil posła" screen.

    Returns every field of `Politician` (the same object as in the camera scan) plus:
    - `gender`: `female` / `male`, for "Posłanka" / "Poseł"
    - `club_label`, `club_name`: short label and the name for the club chip, e.g. `Koalicja Obywatelska`
    - `functions`: committee and club functions, most important first; empty when the MP has none
    - `terms`: Sejm terms the MP served in, e.g. `[8, 9, 10]`
    - `votings_count`: votings held in the current term while the MP had the mandate
    - `votes_cast`: votings the MP took part in (for / against / abstained); `votings_count - votes_cast` = missed
    - `attendance_percent`: `votes_cast / votings_count * 100`, 1 decimal (the "obecność" tile)
    - `votes_cast_all_terms`: `votes_cast` summed over all `terms`
    - `office_address`: always `null` for now

    Full descriptions: the **Schema** tab of the 200 response.

    If the Sejm API fails, the extra fields come back as `null` / `[]` and the endpoint still returns 200.
    The first request for an MP takes 1-3 s; later requests come from the cache.
    """
    try:
        politician = get_politician(mp_id)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Sejm API is unavailable") from exc
    if politician is None:
        raise HTTPException(404, "MP not found")
    return build_profile(_with_photo_url(request, politician))


@router.get(
    "/{mp_id}/photo",
    name="politician_photo",
    summary="Official MP photo (JPEG)",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "JPEG, about 110×137 px"},
        404: {"model": ErrorResponse, "description": "No photo for this id in the Sejm API."},
        **SEJM_DOWN,
    },
)
def photo(mp_id: int) -> Response:
    """Proxied from the Sejm API (which Android does not trust) and cached on our disk."""
    try:
        data = get_photo(mp_id)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Sejm API is unavailable") from exc
    if data is None:
        raise HTTPException(404, "Photo not found")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


@router.post(
    "/recognize",
    summary="Recognize politicians in a photo",
    responses={
        400: {"model": ErrorResponse, "description": "The file is not a decodable image."},
        413: {"model": ErrorResponse, "description": "Photo larger than 10 MB."},
    },
)
def recognize(
    request: Request,
    photo: UploadFile = File(description="Photo from the phone camera (JPEG/PNG/WebP, max 10 MB)."),
) -> RecognizeResponse:
    """Detect faces in the photo and match each against MPs of the current Sejm term.

    The index is built with `python -m scripts.index_politicians`; until then every face comes back
    with `politician: null`. Uploaded photos are not stored.
    """
    data = photo.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Photo larger than 10 MB")
    image = decode_image(data)
    if image is None:
        raise HTTPException(400, "Not an image")

    threshold = get_settings().face_match_threshold
    faces = []
    for face in get_engine().faces(image):
        best = next(iter(search_faces(face.embedding.tolist())), None)
        matched = best is not None and best.similarity >= threshold
        faces.append(
            RecognizedFace(
                box=face.box,
                politician=_with_photo_url(request, best.politician) if matched else None,
                similarity=best.similarity if best else None,
            )
        )
    return RecognizeResponse(faces=faces)
