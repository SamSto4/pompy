"""Test doubles shared by the Phase 2 tests."""

import hashlib

import numpy as np

# Words that mean the same thing share a "concept", which is what a real model learns.
CONCEPTS = {
    "login": "auth", "sign": "auth", "password": "auth", "credentials": "auth", "email": "auth",
    "bug": "defect", "broken": "defect", "error": "defect", "crash": "defect", "fix": "defect",
    "button": "control", "link": "control", "component": "control",
    "pagination": "paging", "pages": "paging", "paging": "paging",
}  # fmt: skip


class FakeEmbedder:
    """Bag of concepts in 64 dimensions: deterministic, instant, no model download."""

    def __init__(self, name="fake-concepts"):
        self.name = name
        self.calls = 0

    def _vec(self, text):
        v = np.zeros(64, dtype=np.float32)
        for word in text.lower().replace('"', " ").split():
            word = word.strip(".,:;!?()…")
            key = CONCEPTS.get(word, word)
            v[int(hashlib.md5(key.encode()).hexdigest(), 16) % 64] += 1
        n = np.linalg.norm(v)
        return v / n if n else v

    def passages(self, texts):
        self.calls += len(texts)
        return np.stack([self._vec(t) for t in texts])

    def query(self, text):
        return self._vec(text)
