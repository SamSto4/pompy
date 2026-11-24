"""Themes: what you keep asking Claude about, found by clustering your task prompts by meaning.

Memory concept: unsupervised structure in episodic memory, the first step toward long-term
memory. Hundreds of separate episodes become a handful of recurring themes, each with how it
tends to go for you (how often something went wrong, how specific your prompts were).

Why prompt-only vectors: the search index (Step 11) embeds each exchange with its context
header, which names the project. Clustering those would mostly group by project. Themes embed
your prompt alone (stored under the model name + "#prompt"), so they group by what you asked.

Method, all local: k-means (k-means++ start, fixed seed, so runs are reproducible) on unit
vectors, k chosen by the silhouette score (how much closer each prompt is to its own theme
than to the nearest other one). Each theme is named by its most distinctive words: frequent
inside the theme, rarer outside it.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter

from pompy import keywords, score

K_RANGE = range(4, 11)  # candidate numbers of themes
MIN_TASKS = 20  # below this, there is too little to cluster
SEED = 7
LABEL_TERMS = 3
EXAMPLES = 3


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def task_prompts(conn: sqlite3.Connection) -> list[dict]:
    """Task prompts with their exchange outcome (same roles and scores as `pompy score`)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT e.prompt_uuid, e.session_id, e.ts, e.prompt, e.friction, p.has_paste, p.has_image "
        "FROM main.excerpts e JOIN main.prompts p ON p.uuid = e.prompt_uuid ORDER BY e.ts"
    ).fetchall()
    out = []
    for r in rows:
        s = score.score_text(r["prompt"], bool(r["has_paste"]), bool(r["has_image"]))
        if s["role"] == "task":
            out.append({**dict(r), "score": s["score"]})
    return out


def prompt_vectors(conn: sqlite3.Connection, embedder, prompts: list[dict]):
    """Unit vectors of the prompts alone, computed only for new or changed prompt text."""
    import numpy as np

    key = f"{embedder.name}#prompt"
    have = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT prompt_uuid, text_hash, vector FROM main.embeddings WHERE model = ?", (key,)
        )
    }
    todo = [p for p in prompts if have.get(p["prompt_uuid"], ("",))[0] != _digest(p["prompt"])]
    for i in range(0, len(todo), 64):
        chunk = todo[i : i + 64]
        vecs = embedder.passages([p["prompt"] for p in chunk])
        rows = [
            (
                p["prompt_uuid"],
                key,
                _digest(p["prompt"]),
                v.shape[0],
                v.astype(np.float32).tobytes(),
            )
            for p, v in zip(chunk, vecs, strict=True)
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings (prompt_uuid, model, text_hash, dim, vector) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        for u, _, h, _, blob in rows:
            have[u] = (h, blob)
    conn.commit()
    return np.stack([np.frombuffer(have[p["prompt_uuid"]][1], dtype=np.float32) for p in prompts])


def kmeans(x, k: int, seed: int = SEED, iterations: int = 50):
    """Spherical k-means (cosine) with a k-means++ start. Returns (labels, centroids)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    centroids = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        dist = 1 - np.max(x @ np.stack(centroids).T, axis=1)
        dist = np.clip(dist, 0, None)
        probs = dist / dist.sum() if dist.sum() else np.full(len(x), 1 / len(x))
        centroids.append(x[rng.choice(len(x), p=probs)])
    c = np.stack(centroids)
    labels = np.zeros(len(x), dtype=int)
    for _ in range(iterations):
        new = np.argmax(x @ c.T, axis=1)
        if _ > 0 and np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            members = x[labels == j]
            if len(members):
                m = members.mean(axis=0)
                c[j] = m / (np.linalg.norm(m) or 1)
    return labels, c


def silhouette(x, labels) -> float:
    """Mean silhouette with cosine distance: near 1 = tight, separate themes; near 0 = blurry."""
    import numpy as np

    d = 1 - x @ x.T
    ks = np.unique(labels)
    if len(ks) < 2:
        return 0.0
    scores = []
    for i in range(len(x)):
        same = labels == labels[i]
        n_same = same.sum() - 1
        if n_same == 0:
            scores.append(0.0)
            continue
        a = d[i, same].sum() / n_same
        b = min(d[i, labels == j].mean() for j in ks if j != labels[i])
        scores.append((b - a) / max(a, b))
    return float(np.mean(scores))


def _label(texts: list[str], all_counts: Counter, n_all: int) -> list[str]:
    """Words frequent in this theme and rarer outside it (counted once per prompt)."""
    inside = Counter(t for text in texts for t in set(keywords.content_terms(text)))
    ranked = sorted(
        (t for t, n in inside.items() if n >= max(2, len(texts) // 10)),
        key=lambda t: (
            -(inside[t] / len(texts)) * (inside[t] / len(texts)) / (all_counts[t] / n_all)
        ),
    )
    return ranked[:LABEL_TERMS]


def compute(conn: sqlite3.Connection, embedder, k: int | None = None) -> dict:
    """Cluster task prompts into themes. `k` fixed, or chosen by silhouette over K_RANGE."""
    import numpy as np

    prompts = task_prompts(conn)
    if len(prompts) < MIN_TASKS:
        return {"themes": [], "k": 0, "prompts": len(prompts), "note": "too few task prompts"}
    x = prompt_vectors(conn, embedder, prompts)
    if k is None:
        tried = {}
        for kk in K_RANGE:
            if kk >= len(prompts):
                break
            labels, _ = kmeans(x, kk)
            tried[kk] = silhouette(x, labels)
        k = max(tried, key=tried.get)
    else:
        tried = {}
    labels, centroids = kmeans(x, k)

    all_counts = Counter(t for p in prompts for t in set(keywords.content_terms(p["prompt"])))
    themes = []
    for j in range(k):
        idx = [i for i in range(len(prompts)) if labels[i] == j]
        if not idx:
            continue
        members = [prompts[i] for i in idx]
        sims = x[idx] @ centroids[j]
        central = [members[i] for i in np.argsort(-sims)[:EXAMPLES]]
        wrong = sum(1 for m in members if m["friction"])
        kinds = Counter(f for m in members for f in m["friction"].split(",") if f)
        months = Counter(m["ts"][:7] for m in members)
        themes.append(
            {
                "id": j,
                "label": _label([m["prompt"] for m in members], all_counts, len(prompts)),
                "size": len(members),
                "share_pct": round(100 * len(members) / len(prompts), 1),
                "went_wrong_pct": round(100 * wrong / len(members), 1),
                "top_friction": kinds.most_common(1)[0][0] if kinds else None,
                "mean_score": round(sum(m["score"] for m in members) / len(members), 2),
                "chats": len({m["session_id"] for m in members}),
                "months": dict(sorted(months.items())),
                "examples": [
                    {"uuid": m["prompt_uuid"], "prompt": m["prompt"][:200]} for m in central
                ],
                "members": [m["prompt_uuid"] for m in members],
            }
        )
    themes.sort(key=lambda t: -t["size"])
    return {
        "k": k,
        "prompts": len(prompts),
        "silhouette": round(silhouette(x, labels), 3),
        "k_tried": {str(kk): round(v, 3) for kk, v in tried.items()},
        "model": f"{embedder.name}#prompt",
        "themes": themes,
    }


def render(result: dict) -> str:
    if not result["themes"]:
        return f"No themes yet: {result.get('note', 'nothing to cluster')}."
    out = [
        f"{result['k']} themes across {result['prompts']} task prompts "
        f"(silhouette {result['silhouette']}; "
        f"k chosen from {', '.join(result['k_tried']) or 'fixed'})",
        "",
    ]
    for t in result["themes"]:
        name = " · ".join(t["label"]) or "(mixed)"
        out.append(
            f"  {name:<34} {t['size']:>4} prompts ({t['share_pct']}%)  "
            f"went wrong {t['went_wrong_pct']}%  specificity {t['mean_score']}/7"
        )
        out.append(f"      e.g. {t['examples'][0]['prompt'][:110]}")
    return "\n".join(out)
