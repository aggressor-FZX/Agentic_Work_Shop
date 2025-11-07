# memory_store.py
from __future__ import annotations
import json, time, hashlib
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from redis import Redis
from sentence_transformers import SentenceTransformer

# Namespace → (K/V store + vector index)
class UnifiedMemory:
    def __init__(self, host="127.0.0.1", port=6379, ns="default"):
        self.r = Redis(host=host, port=port, decode_responses=False)
        self.ns = ns
        self.embed = SentenceTransformer("all-MiniLM-L6-v2")

    def _k(self, suffix: str) -> str:
        return f"um:{self.ns}:{suffix}"

    # ---- CRUD ----
    def put(self, key: str, value: Dict[str, Any], ttl_sec: Optional[int]=None) -> str:
        b = json.dumps(value).encode()
        self.r.hset(self._k("kv"), key, b)
        if ttl_sec:
            self.r.expire(self._k("kv"), ttl_sec)
        # also index it in vectors
        text = value.get("text") or value.get("summary") or json.dumps(value)[:1000]
        emb = self.embed.encode([text], normalize_embeddings=True)[0].astype(np.float32).tobytes()
        self.r.hset(self._k("vec"), key, emb)
        return "OK"

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        b = self.r.hget(self._k("kv"), key)
        return json.loads(b.decode()) if b else None

    def delete(self, key: str) -> bool:
        a = self.r.hdel(self._k("kv"), key)
        b = self.r.hdel(self._k("vec"), key)
        return (a+b) > 0

    # ---- semantic search (cosine) ----
    def search(self, query: str, topk=5) -> List[Tuple[str, float, Dict[str, Any]]]:
        q = self.embed.encode([query], normalize_embeddings=True)[0].astype(np.float32)
        vecs = self.r.hgetall(self._k("vec"))
        if not vecs: return []
        scores = []
        for k, vb in vecs.items():
            v = np.frombuffer(vb, dtype=np.float32)
            score = float(np.dot(q, v))  # cosine because normalized
            scores.append((k.decode(), score))
        scores.sort(key=lambda x: x[1], reverse=True)
        out = []
        kv = self.r.hgetall(self._k("kv"))
        for k, s in scores[:topk]:
            val = json.loads(kv[k.encode()].decode())
            out.append((k, s, val))
        return out

# Test
if __name__ == "__main__":
    print("UnifiedMemory class loaded")
