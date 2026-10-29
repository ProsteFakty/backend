"""Download MP photos from the Sejm API, embed their faces and upsert them into Qdrant.

Idempotent, re-run after MP changes:
    docker compose exec backend python -m scripts.index_politicians
"""
import httpx

from app.config import get_settings
from app.services.faces import decode_image, get_engine
from app.services.politicians import SEJM_API, Politician, ensure_collection, upsert_faces

BATCH = 100
RETRIES = 3


def fetch_photo(sejm: httpx.Client, url: str) -> bytes | None:
    # api.sejm.gov.pl occasionally hangs on single requests; one timeout shouldn't kill a 500-MP run.
    for _ in range(RETRIES):
        try:
            r = sejm.get(url)
            return r.content if r.status_code == 200 else None
        except httpx.TransportError:
            continue
    return None


def main() -> None:
    term = get_settings().sejm_term
    engine = get_engine()
    ensure_collection()

    with httpx.Client(base_url=SEJM_API, timeout=30) as sejm:
        mps = sejm.get(f"/term{term}/MP").raise_for_status().json()
        batch, indexed, skipped = [], 0, []
        for i, mp in enumerate(mps, 1):
            politician = Politician.from_sejm(mp, term)
            image = decode_image(fetch_photo(sejm, f"/term{term}/MP/{mp['id']}/photo") or b"")
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
