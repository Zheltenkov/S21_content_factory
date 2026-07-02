"""Класс для вычисления семантического сходства между текстами."""

import hashlib
from collections.abc import Sequence
from math import sqrt

from ...utils.logging import safe_print
from .utils import bag, cosine, tokens


class SimilarityCalculator:
    """Калькулятор семантического сходства с поддержкой SBERT и batch processing."""

    def __init__(self, embedding_function=None, language: str = "ru", enable_cache: bool = True):
        """
        Инициализация калькулятора.
        
        Args:
            embedding_function: Функция для создания эмбеддингов (SBERT)
            language: Язык текстов
            enable_cache: Включить кэширование эмбеддингов (по умолчанию True)
        """
        self.embedding_function = embedding_function
        self.lang = language
        self.enable_cache = enable_cache
        self._embedding_cache: dict[str, Sequence[float]] = {}

    def _cosine_sim(self, va: Sequence[float], vb: Sequence[float]) -> float:
        """Вычисляет косинусное сходство между двумя векторами."""
        if va is None or vb is None or len(va) == 0 or len(vb) == 0:
            return 0.0

        dot_product = sum(a * b for a, b in zip(va, vb, strict=False))
        norm_a = sqrt(sum(a * a for a in va))
        norm_b = sqrt(sum(b * b for b in vb))

        if norm_a > 0 and norm_b > 0:
            return dot_product / (norm_a * norm_b)
        return 0.0

    def _get_text_hash(self, text: str) -> str:
        """Создает хеш текста для кэширования."""
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    def _embed_many(self, texts: list[str]) -> list[Sequence[float]] | None:
        """Создает эмбеддинги для списка текстов (batch processing) с кэшированием."""
        if not self.embedding_function or not texts:
            return None

        try:
            # Фильтруем пустые тексты
            non_empty_texts = [t for t in texts if t and t.strip()]
            if not non_empty_texts:
                return None

            # Проверяем кэш и собираем тексты для эмбеддинга
            texts_to_embed = []
            text_indices = []
            cached_vectors = []

            if self.enable_cache:
                for i, text in enumerate(non_empty_texts):
                    text_hash = self._get_text_hash(text)
                    if text_hash in self._embedding_cache:
                        cached_vectors.append((i, self._embedding_cache[text_hash]))
                    else:
                        texts_to_embed.append((i, text))
            else:
                texts_to_embed = [(i, text) for i, text in enumerate(non_empty_texts)]

            # Создаем эмбеддинги только для текстов, которых нет в кэше
            vectors = None
            if texts_to_embed:
                texts_only = [text for _, text in texts_to_embed]
                vectors = self.embedding_function(texts_only)
                if vectors is not None:
                    try:
                        vectors_len = len(vectors)
                    except (TypeError, ValueError):
                        vectors_len = 0

                    if vectors_len == len(texts_only):
                        # Сохраняем в кэш
                        if self.enable_cache:
                            for (i, text), vector in zip(texts_to_embed, vectors, strict=False):
                                text_hash = self._get_text_hash(text)
                                self._embedding_cache[text_hash] = vector
                    else:
                        return None
                else:
                    return None

            # Собираем все векторы в правильном порядке
            result_vectors = [None] * len(non_empty_texts)

            # Добавляем закэшированные векторы
            for i, vector in cached_vectors:
                result_vectors[i] = vector

            # Добавляем новые векторы
            if vectors is not None:
                for (i, _), vector in zip(texts_to_embed, vectors, strict=False):
                    result_vectors[i] = vector

            # Проверяем, что все векторы получены
            if any(v is None for v in result_vectors):
                return None

            return result_vectors
        except Exception as e:
            safe_print(f"[SIMILARITY] Ошибка batch embedding: {e}", flush=True)
            return None

    def sbert_similarity(self, a: str, b: str) -> float | None:
        """
        Считает cosine similarity между двумя текстами с помощью SBERT.
        Если embedding_function не доступен, возвращает None.
        """
        if not self.embedding_function:
            return None

        try:
            vectors: Sequence[Sequence[float]] = self.embedding_function([a, b])
            va, vb = vectors[0], vectors[1]
            return self._cosine_sim(va, vb)
        except Exception as e:
            safe_print(f"[SIMILARITY] SBERT similarity failed: {e}", flush=True)
            return None

    def text_similarity(self, a: str, b: str, use_sbert: bool = True) -> float:
        """
        Универсальный метод для вычисления similarity между двумя текстами.
        Использует SBERT если доступен, иначе fallback на bag-of-words.
        
        Args:
            a: Первый текст
            b: Второй текст
            use_sbert: Использовать ли SBERT (если доступен)
        
        Returns:
            Similarity score (0..1)
        """
        if use_sbert and self.embedding_function:
            similarity = self.sbert_similarity(a, b)
            if similarity is not None:
                return similarity

        # Fallback на bag-of-words
        va = bag(tokens(a, self.lang))
        vb = bag(tokens(b, self.lang))
        return cosine(va, vb)

    def compute_pairwise_similarities(
        self,
        reference_text: str,
        texts: list[str],
        use_batch_embedding: bool = True
    ) -> tuple[float, list[float]]:
        """
        Универсальный метод для вычисления similarity между референсным текстом и списком текстов.
        Оптимизирован: использует batch embedding для всех текстов сразу.
        
        Args:
            reference_text: Референсный текст (например, глава 2 или предыдущий абзац)
            texts: Список текстов для сравнения (например, задачи или следующие абзацы)
            use_batch_embedding: Использовать ли batch embedding (оптимизация)
        
        Returns:
            avg_score: средний score
            scores: список scores по каждому тексту
        """
        if not texts or not reference_text.strip():
            return 0.0, []

        # Пытаемся использовать batch embedding для оптимизации
        if use_batch_embedding and self.embedding_function:
            embeddings = self._embed_many([reference_text] + texts)
            if embeddings is not None:
                try:
                    embeddings_len = len(embeddings)
                except (TypeError, ValueError):
                    embeddings_len = 0

                if embeddings_len >= len(texts) + 1:
                    v_ref = embeddings[0]
                    scores: list[float] = []
                    for i in range(len(texts)):
                        v_text = embeddings[i + 1]
                        sim = self._cosine_sim(v_ref, v_text)
                        scores.append(sim)

                    avg = sum(scores) / len(scores) if scores else 0.0
                    return avg, scores

        # Fallback: вычисляем similarity для каждого текста отдельно
        scores: list[float] = []
        for text in texts:
            sim = self.text_similarity(reference_text, text, use_sbert=use_batch_embedding)
            scores.append(sim)

        avg = sum(scores) / len(scores) if scores else 0.0
        return avg, scores

    def compute_sequential_similarities(
        self,
        texts: list[str],
        use_batch_embedding: bool = True
    ) -> tuple[float, list[float]]:
        """
        Универсальный метод для вычисления similarity между соседними текстами в списке.
        Оптимизирован: использует batch embedding для всех пар сразу.
        
        Args:
            texts: Список текстов для последовательного сравнения (например, абзацы)
            use_batch_embedding: Использовать ли batch embedding (оптимизация)
        
        Returns:
            avg_score: средний score
            pairwise_scores: список scores для каждой пары (texts[i] ↔ texts[i+1])
        """
        if len(texts) < 2:
            return 0.0, []

        # Пытаемся использовать batch embedding для оптимизации
        if use_batch_embedding and self.embedding_function:
            # Создаем пары для сравнения
            pairs = [(texts[i], texts[i+1]) for i in range(len(texts) - 1)]
            # Подготавливаем все тексты для batch embedding
            all_texts = []
            for a, b in pairs:
                all_texts.extend([a, b])

            embeddings = self._embed_many(all_texts)
            if embeddings is not None:
                try:
                    embeddings_len = len(embeddings)
                except (TypeError, ValueError):
                    embeddings_len = 0

                if embeddings_len >= len(all_texts):
                    scores: list[float] = []
                    for i in range(len(pairs)):
                        v_a = embeddings[i * 2]
                        v_b = embeddings[i * 2 + 1]
                        sim = self._cosine_sim(v_a, v_b)
                        scores.append(sim)

                    avg = sum(scores) / len(scores) if scores else 0.0
                    return avg, scores

        # Fallback: вычисляем similarity для каждой пары отдельно
        scores: list[float] = []
        for a, b in zip(texts, texts[1:], strict=False):
            sim = self.text_similarity(a, b, use_sbert=use_batch_embedding)
            scores.append(sim)

        avg = sum(scores) / len(scores) if scores else 0.0
        return avg, scores

