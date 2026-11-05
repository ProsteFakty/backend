from typing import ClassVar

from pydantic import BaseModel
from qdrant_client import QdrantClient, models

# LangChain's QdrantVectorStore keeps Document.metadata under this payload key.
METADATA_KEY = "metadata"


class QdrantCollection(BaseModel):
    """A Qdrant collection described as a Pydantic model.

    Subclasses set the collection name and which payload fields get an index; the model fields are
    the metadata every point carries (stored by LangChain under `metadata.*`).
    """

    collection_name: ClassVar[str]
    indexed_fields: ClassVar[dict[str, models.PayloadSchemaType]] = {}
    distance: ClassVar[models.Distance] = models.Distance.COSINE

    @classmethod
    def ensure(cls, client: QdrantClient, vector_size: int) -> None:
        if not client.collection_exists(cls.collection_name):
            client.create_collection(
                cls.collection_name,
                vectors_config=models.VectorParams(size=vector_size, distance=cls.distance),
            )
        for name, schema in cls.indexed_fields.items():
            client.create_payload_index(cls.collection_name, field_name=f"{METADATA_KEY}.{name}", field_schema=schema)

    @classmethod
    def field_filter(cls, **values: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(key=f"{METADATA_KEY}.{name}", match=models.MatchValue(value=value))
                for name, value in values.items()
            ]
        )


class ActArticleChunk(QdrantCollection):
    """One embedded part of an article summary. The embedded text is "<act title>\\nArt. N\\n<summary part>"."""

    collection_name: ClassVar[str] = "act_articles"
    indexed_fields: ClassVar[dict[str, models.PayloadSchemaType]] = {
        "slug": models.PayloadSchemaType.KEYWORD,
        "act_key": models.PayloadSchemaType.KEYWORD,
        "article_number": models.PayloadSchemaType.KEYWORD,
    }

    slug: str  # article slug, same as articles.slug in Postgres: "ustawa_..._2008_12_06_art_99b"
    act_key: str  # slug prefix of the act: "ustawa_o_podatku_akcyzowym_2008_12_06"
    act_title: str
    article_number: str
    part: int
    part_count: int
    summary: str  # full article summary, so any matching part can show it
    source_sha256: str
