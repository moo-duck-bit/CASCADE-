"""고정 코퍼스 규정 검색. 코퍼스는 도메인 팩의 corpus 설정(EC-SW: pilot_data/corpus/regs.jsonl, 해시 regs.sha256).

검색기 3종이 같은 인터페이스 search(query, k) → [{chunk_id, clause_id, source, page, section, text, score}]를 낸다.
- bm25: 글자 2-gram + 공백 낱말 토큰 BM25(형태소 분석기 없이 한국어에 쓰기 위해)
- dense: 공개 다국어 임베딩(BGE-M3). 청크 임베딩은 코퍼스 해시로 이름 붙인 캐시 파일에서 읽고, 질의 임베딩은
  embed_fn(기본: 로컬 임베딩 서버 HTTP)으로 만든다.
- hybrid: 두 점수를 질의마다 최소-최대 정규화해 alpha:1-alpha로 더한다.
코퍼스 해시가 고정값(regs.sha256)과 다르면 CorpusHashError. 검색 호출 수는 retriever.calls로 센다.
"""

import hashlib
import json
import math
import re
import urllib.request
from collections import Counter
from pathlib import Path

EMBED_MODEL = "BAAI/bge-m3"


class CorpusHashError(RuntimeError):
    pass


def _read(path, reader):
    data = reader(path) if reader else Path(path).read_bytes()
    return data if isinstance(data, bytes) else data.encode("utf-8")


def load_corpus(corpus_dir, regs="regs.jsonl", hash_file="regs.sha256", reader=None):
    corpus_dir = Path(corpus_dir)
    data = _read(corpus_dir / regs, reader)
    want = _read(corpus_dir / hash_file, reader).decode("utf-8").split()[0]
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise CorpusHashError(f"corpus hash {got[:12]} != fixed {want[:12]}")
    chunks = [json.loads(ln) for ln in data.decode("utf-8").splitlines() if ln.strip()]
    return chunks, got


def tokens(text):
    t = re.sub(r"\s+", " ", text.lower()).strip()
    words = re.findall(r"[0-9a-z가-힣\-]+", t)
    grams = [t[i:i + 2] for i in range(len(t) - 1) if " " not in t[i:i + 2]]
    return words + grams


def _hit(c, score):
    return {"chunk_id": c["chunk_id"], "clause_id": c["clause_id"], "source": c["source"], "page": c["page"],
            "section": c.get("section", ""), "sec_id": c.get("sec_id", ""), "text": c["text"], "score": score}


class _Base:
    name = ""

    def __init__(self):
        self.calls = 0

    def search(self, query, k):
        self.calls += 1
        scores = self.scores(query)
        order = sorted(range(len(self.chunks)), key=lambda i: (-scores[i], i))[:k]
        return [_hit(self.chunks[i], round(scores[i], 6)) for i in order]


class BM25Retriever(_Base):
    name = "bm25"

    def __init__(self, chunks, k1=1.5, b=0.75):
        super().__init__()
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(f"{c.get('section', '')} {c['text']}")) for c in chunks]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(1, len(self.lens))
        df = Counter(t for d in self.docs for t in d)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query):
        q = Counter(tokens(query))
        out = []
        for d, ln in zip(self.docs, self.lens):
            s = 0.0
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
            out.append(s)
        return out


def http_embed(url, timeout=120):
    def embed(texts):
        req = urllib.request.Request(url, data=json.dumps({"texts": texts}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["vectors"]
    return embed


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cache_path(corpus_dir, corpus_hash, model=EMBED_MODEL):
    return Path(corpus_dir) / "cache" / f"{model.split('/')[-1]}_{corpus_hash[:12]}.jsonl"


class DenseRetriever(_Base):
    name = "dense"

    def __init__(self, chunks, embed_fn, vectors=None):
        super().__init__()
        self.chunks = chunks
        self.embed_fn = embed_fn
        if vectors is None:
            vectors = embed_fn([f"{c.get('section', '')} {c['text']}" for c in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("embedding count != chunk count")
        self.vectors = [_norm(v) for v in vectors]

    def scores(self, query):
        q = _norm(self.embed_fn([query])[0])
        return [sum(a * b for a, b in zip(q, v)) for v in self.vectors]


def _minmax(xs):
    lo, hi = min(xs), max(xs)
    return [0.0 if hi == lo else (x - lo) / (hi - lo) for x in xs]


class HybridRetriever(_Base):
    name = "hybrid"

    def __init__(self, bm25, dense, alpha=0.5):
        super().__init__()
        self.chunks = bm25.chunks
        self.bm25, self.dense, self.alpha = bm25, dense, alpha

    def scores(self, query):
        a, b = _minmax(self.bm25.scores(query)), _minmax(self.dense.scores(query))
        return [self.alpha * x + (1 - self.alpha) * y for x, y in zip(a, b)]


def load_vectors(path, reader=None):
    data = _read(path, reader).decode("utf-8")
    return [json.loads(ln)["v"] for ln in data.splitlines() if ln.strip()]


def make_retriever(config, pack, reader=None, embed_fn=None):
    """설정(retriever, embed_url)과 팩 코퍼스로 검색기를 만든다. retrieval이 꺼져 있으면 None."""
    if not config.retrieval or not pack.corpus:
        return None
    cdir = pack.corpus_dir()
    chunks, h = load_corpus(cdir, pack.corpus["regs"], pack.corpus["hash"], reader)
    if config.retriever == "bm25":
        return BM25Retriever(chunks)
    embed_fn = embed_fn or http_embed(config.embed_url)
    vectors = load_vectors(cache_path(cdir, h), reader)
    dense = DenseRetriever(chunks, embed_fn, vectors)
    if config.retriever == "dense":
        return dense
    return HybridRetriever(BM25Retriever(chunks), dense)


def evidence_text(hits, max_chars=300):
    lines = ["## 관련 규정 근거(고정 코퍼스 검색)"]
    for h in hits:
        lines.append(f"- [{h['clause_id']}] ({h['source']} {h['page']}쪽, {h['section']}) {h['text'][:max_chars]}")
    return "\n".join(lines) + "\n"
