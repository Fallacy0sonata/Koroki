"""Retrieval-Augmented Generation (RAG) system for current events and web knowledge."""

from .engine import (
    inject_rag_facts,
    search_web,
)

__all__ = [
    "search_web",
    "inject_rag_facts",
]
