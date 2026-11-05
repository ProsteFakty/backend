from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings
from qdrant_client import QdrantClient
from transformers import AutoTokenizer, PreTrainedTokenizerBase

from app.config import get_settings


@lru_cache
def get_embeddings() -> HuggingFaceEmbeddings:
    # Downloaded from Hugging Face on first use (~2 GB for bge-m3), then cached.
    return HuggingFaceEmbeddings(
        model_name=get_settings().embedding_model,
        encode_kwargs={"normalize_embeddings": True},
    )


@lru_cache
def get_tokenizer() -> PreTrainedTokenizerBase:
    return AutoTokenizer.from_pretrained(get_settings().embedding_model)


@lru_cache
def get_embedding_size() -> int:
    return len(get_embeddings().embed_query("rozmiar"))


@lru_cache
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=get_settings().qdrant_url)
