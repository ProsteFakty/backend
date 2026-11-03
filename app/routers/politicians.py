from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.faces import decode_image, get_engine
from app.services.politicians import (
    Politician,
    PoliticianSummary,
    get_politician,
    search_faces,
    search_politicians,
)

router = APIRouter(prefix="/politicians", tags=["politicians"])

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
                                "voivodeship": "małopolskie",
                                "birth_date": "1959-01-04",
                                "birth_location": "Krzeszowice",
                                "profession": "ekonomista",
                                "education": "wyższe",
                                "email": "Andrzej.Adamczyk@sejm.pl",
                                "number_of_votes": 45171,
                                "active": True,
                                "photo_url": "https://api.sejm.gov.pl/sejm/term10/MP/1/photo",
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


@router.get("/search", summary="Search MPs by name")
def search(
    q: str = Query(min_length=2, description="Name fragment(s), e.g. `adam`, `kowal jan`. Polish characters optional."),
    limit: int = Query(20, ge=1, le=100),
) -> list[PoliticianSummary]:
    """Matches word prefixes of first and last name; MPs whose last name starts with the query come first.

    Open a result with `GET /api/politicians/{mp_id}`.
    """
    return [PoliticianSummary(**p.model_dump()) for p in search_politicians(q, limit)]


@router.get(
    "/{mp_id}",
    summary="MP profile",
    responses={404: {"model": ErrorResponse, "description": "No MP with this id in the current term."}},
)
def profile(mp_id: int) -> Politician:
    """Same object as `politician` in the camera scan response."""
    politician = get_politician(mp_id)
    if politician is None:
        raise HTTPException(404, "MP not found")
    return politician


@router.post(
    "/recognize",
    summary="Recognize politicians in a photo",
    responses={
        400: {"model": ErrorResponse, "description": "The file is not a decodable image."},
        413: {"model": ErrorResponse, "description": "Photo larger than 10 MB."},
    },
)
def recognize(
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
                politician=best.politician if matched else None,
                similarity=best.similarity if best else None,
            )
        )
    return RecognizeResponse(faces=faces)
