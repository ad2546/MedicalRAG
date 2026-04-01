"""Singleton wrapper around sentence-transformers for local embedding generation."""

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import settings


@lru_cache(maxsize=1)
def _load_model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model)


class EmbeddingService:
    """Generates embeddings using all-MiniLM-L6-v2 (384 dimensions)."""

    def embed(self, text: str) -> list[float]:
        model = _load_model()
        return model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        model = _load_model()
        return model.encode(
            texts,
            batch_size=settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

    def build_query(self, symptoms: list[str], hint: str | None = None) -> str:
        query = "Patient presents with: " + ", ".join(symptoms)
        if hint:
            query += f". Additional context: {hint}"
        return query


embedding_service = EmbeddingService()
