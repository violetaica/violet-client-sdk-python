"""Minimal RAG (retrieval-augmented generation) with no external vector DB.

What this shows
---------------
A complete, dependency-free RAG loop:
  1. A tiny in-memory corpus of ~5 plain-string "documents".
  2. A naive keyword-overlap retriever (pure stdlib) that scores each document
     against the question and returns the top-k.
  3. A single `client.messages.create(...)` call whose system prompt pins the
     model to the retrieved context and whose user prompt carries that context
     plus the question. The model is told to answer ONLY from the context and to
     say so when the answer isn't present.

How to run
----------
        python cookbook/rag_minimal.py

Prereqs: a paid Violet workspace (Auth0 login or `VIOLET_API_KEY`), a
one-time browser login on the first call, The gateway sets the model.
"""
import os
import re
from collections import Counter

from violet import APIError, Violet, VioletError

# Placeholder — API requires model=; gateway sets the served model (client cannot).
DEFAULT_MODEL = os.environ.get("MESSAGES_TEST_MODEL", "Violet Test Model")

# A miniature "knowledge base" — in a real app these would come from a store.
DOCUMENTS = [
    "The Violet SDK exposes client.messages.create and "
    "client.messages.stream for chat and streaming replies.",
    "Authentication in Violet is keyless. Instead of an API key, the first call opens a "
    "browser for an Auth0 PKCE login and then caches the token locally.",
    "Every Violet request is scoped to a workspace. The workspace id comes from the "
    "WORKSPACE_ID environment variable or is resolved from your default workspace.",
    "Streaming responses use a context manager: `with client.messages.stream(...) as s` "
    "and you iterate s.text_stream for incremental text deltas.",
    "Usage metering is a Violet extension: client.usage.get() returns a monthly "
    "USD billing snapshot for the workspace, not per-request token totals.",
]

# Tokens shorter than this or in the stop list are ignored when scoring.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "for",
    "how", "do", "does", "what", "with", "you", "your", "it", "on", "that",
}


def _tokenize(text):
    """Lowercase word tokens, minus stopwords and 1-char noise (pure stdlib)."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if len(w) > 1 and w not in _STOPWORDS]


def retrieve(question, documents, k=2):
    """Return the top-k documents by keyword overlap with the question.

    Scoring is intentionally simple: count how many question tokens appear in
    each document (a tiny bag-of-words overlap). No embeddings, no vector DB.
    """
    q_tokens = set(_tokenize(question))
    scored = []
    for doc in documents:
        doc_counts = Counter(_tokenize(doc))
        score = sum(doc_counts[t] for t in q_tokens)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _score, doc in scored[:k]]


def build_prompt(question, context_docs):
    """Render retrieved documents as a numbered context block for the user turn."""
    if not context_docs:
        context = "(no relevant context found)"
    else:
        context = "\n".join(f"[{i}] {doc}" for i, doc in enumerate(context_docs, 1))
    return (
        f"Context:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer using only the context above."
    )


def main():
    question = "How does authentication work in the Violet SDK?"

    context_docs = retrieve(question, DOCUMENTS, k=2)
    print("Retrieved context:")
    for doc in context_docs:
        print(f"  - {doc[:80]}...")
    print()

    client = Violet()
    system = (
        "You are a precise assistant. Answer the user's question using ONLY the "
        "provided context. If the answer is not contained in the context, reply "
        "exactly: 'I don't know based on the provided context.' Do not use outside "
        "knowledge and do not speculate."
    )

    try:
        msg = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": build_prompt(question, context_docs)}],
        )
    except APIError as e:
        print(f"API error {e.status_code}: {e.message}")
        return
    except VioletError as e:
        print(f"Error: {e}")
        return

    print("Answer:")
    print(msg.text())


if __name__ == "__main__":
    main()
