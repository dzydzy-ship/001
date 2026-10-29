"""语义匹配：MVP 用无依赖的 TF-IDF 余弦相似度，生产环境替换为 Embedding + pgvector。

设计上把相似度计算隔离在此文件，未来换成向量检索只需替换 semantic_similarity 实现。
"""
import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    """中文按单字、英文按词切分。"""
    text = text.lower()
    return re.findall(r"[a-z0-9]+|[一-鿿]", text)


def _idf(all_tokens: list[str]) -> dict:
    n_docs = len(set(all_tokens)) or 1
    counter = Counter(all_tokens)
    return {t: math.log(n_docs / (1 + 1)) + 1.0 for t in set(all_tokens)}, counter


def semantic_similarity(text_a: str, text_b: str) -> float:
    """返回 0~1 的余弦相似度（带 IDF 加权的词袋向量）。"""
    ta, tb = tokenize(text_a), tokenize(text_b)
    if not ta or not tb:
        return 0.0
    vocab = set(ta) | set(tb)
    ca, cb = Counter(ta), Counter(tb)
    la, lb = len(ta) or 1, len(tb) or 1

    va = {t: (ca[t] / la) * (math.log(2.0 / ((1 if t in ta else 0) + (1 if t in tb else 0))) + 1.0) for t in vocab}
    vb = {t: (cb[t] / lb) * (math.log(2.0 / ((1 if t in ta else 0) + (1 if t in tb else 0))) + 1.0) for t in vocab}

    dot = sum(va[t] * vb[t] for t in vocab)
    na = math.sqrt(sum(v * v for v in va.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in vb.values())) or 1.0
    return dot / (na * nb)
