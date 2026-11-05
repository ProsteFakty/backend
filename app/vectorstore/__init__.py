from langchain_qdrant import QdrantVectorStore

from app.vectorstore.collections import ActArticleChunk, QdrantCollection
from app.vectorstore.embeddings import get_embedding_size, get_embeddings, get_qdrant_client


def get_vector_store(collection: type[QdrantCollection]) -> QdrantVectorStore:
    client = get_qdrant_client()
    collection.ensure(client, get_embedding_size())
    return QdrantVectorStore(client=client, collection_name=collection.collection_name, embedding=get_embeddings())


__all__ = ["ActArticleChunk", "QdrantCollection", "get_vector_store"]
