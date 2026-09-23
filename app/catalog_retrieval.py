import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .catalog_models import RagDocument


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOCUMENTS_PATH = ROOT / "catalog" / "generated" / "rag_documents.jsonl"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _tokens(text: str) -> list[str]:
    normalized = _normalize(text)
    tokens: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", normalized):
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                tokens.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
        else:
            tokens.extend(part for part in chunk.split("_") if part)
            tokens.append(chunk)
    return tokens


def _ngrams(text: str, size: int = 2) -> set[str]:
    compact = re.sub(r"\W+", "", _normalize(text))
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index:index + size] for index in range(len(compact) - size + 1)}


@dataclass(frozen=True)
class SearchResult:
    document: RagDocument
    score: float
    lexical_score: float
    overlap_score: float
    alias_boost: float

    def as_dict(self) -> dict:
        return {
            "id": self.document.id,
            "document_type": self.document.document_type,
            "title": self.document.title,
            "text": self.document.text,
            "score": round(self.score, 4),
            "score_details": {
                "lexical": round(self.lexical_score, 4),
                "character_overlap": round(self.overlap_score, 4),
                "alias_boost": round(self.alias_boost, 4),
            },
            "metadata": self.document.metadata,
        }


class CatalogRetriever:
    def __init__(self, documents: list[RagDocument]):
        self.documents = documents
        self.token_counts = [Counter(_tokens(f"{item.title} {item.text}")) for item in documents]
        self.document_grams = [_ngrams(f"{item.title} {item.text}") for item in documents]
        self.lengths = [sum(counts.values()) for counts in self.token_counts]
        self.average_length = sum(self.lengths) / max(len(self.lengths), 1)
        document_frequency: Counter[str] = Counter()
        for counts in self.token_counts:
            document_frequency.update(counts.keys())
        self.document_frequency = document_frequency

    @classmethod
    def from_jsonl(cls, path: Path = DEFAULT_DOCUMENTS_PATH):
        documents = [
            RagDocument.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(documents)

    def _bm25(self, query_tokens: list[str], index: int) -> float:
        counts = self.token_counts[index]
        length = self.lengths[index]
        score = 0.0
        k1, b = 1.5, 0.75
        for token in set(query_tokens):
            frequency = counts[token]
            if not frequency:
                continue
            df = self.document_frequency[token]
            inverse_frequency = math.log(1 + (len(self.documents) - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1 - b + b * length / max(self.average_length, 1))
            score += inverse_frequency * frequency * (k1 + 1) / denominator
        return score

    @staticmethod
    def _alias_boost(query: str, document: RagDocument) -> float:
        query_normalized = re.sub(r"\W+", "", _normalize(query))
        aliases = document.metadata.get("aliases") or document.metadata.get("business_names") or []
        for alias in aliases:
            alias_normalized = re.sub(r"\W+", "", _normalize(str(alias)))
            if alias_normalized and alias_normalized in query_normalized:
                return 2.5 if alias_normalized == query_normalized else 1.5
        return 0.0

    def search(
        self,
        query: str,
        limit: int = 8,
        document_types: set[str] | None = None,
        source_id: str | None = None,
    ) -> list[SearchResult]:
        query_tokens = _tokens(query)
        query_grams = _ngrams(query)
        results: list[SearchResult] = []
        for index, document in enumerate(self.documents):
            if document_types and document.document_type not in document_types:
                continue
            if source_id and document.source_id != source_id:
                continue
            lexical = self._bm25(query_tokens, index)
            overlap = len(query_grams & self.document_grams[index]) / max(len(query_grams), 1)
            alias_boost = self._alias_boost(query, document)
            score = lexical + overlap * 2 + alias_boost
            if score > 0:
                results.append(SearchResult(document, score, lexical, overlap, alias_boost))
        return sorted(results, key=lambda item: (-item.score, item.document.id))[:limit]
