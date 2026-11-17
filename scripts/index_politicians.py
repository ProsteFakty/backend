"""Download MP photos from the Sejm API, embed their faces and upsert them into Qdrant.

Idempotent, re-run after MP changes:
    docker compose exec backend python -m scripts.index_politicians

Photos land in the same disk cache GET /api/v1/politicians/{mp_id}/photo serves from.
"""
from app.config import get_settings
from app.services.faces import decode_image, get_engine
from app.services.politicians import (
    SejmUnavailableError,
    ensure_collection,
    get_photo,
    list_politicians,
    upsert_faces,
)

BATCH = 100


def main() -> None:
    term = get_settings().sejm_term
    engine = get_engine()
    ensure_collection()

    mps = list_politicians()
    batch, indexed, skipped = [], 0, []
    for i, politician in enumerate(mps, 1):
        try:
            photo = get_photo(politician.mp_id)
        except SejmUnavailableError:
            photo = None  # one failed download should not stop a 500-MP run; rerun later to fill it in
        image = decode_image(photo or b"")
        faces = engine.faces(image) if image is not None else []
        if not faces:
            skipped.append(politician.full_name)
            continue

        batch.append((politician, faces[0].embedding.tolist()))
        if len(batch) == BATCH:
            upsert_faces(batch)
            indexed += len(batch)
            batch = []
            print(f"{i}/{len(mps)}", flush=True)

    if batch:
        upsert_faces(batch)
        indexed += len(batch)

    print(f"Indexed {indexed} MPs of term {term}, skipped {len(skipped)}: {', '.join(skipped) or '-'}")


if __name__ == "__main__":
    main()
