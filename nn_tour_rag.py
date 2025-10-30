import os
import sys
import io

if sys.platform == "win32":
    import codecs
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer)
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
import re
import json
import math
import time
import pathlib
from typing import List, Tuple, Dict, Any, Optional

import numpy as np
import pandas as pd
import requests
from openai import OpenAI


DATASET_PATH = "./cultural_objects_mnn.xlsx"

DEEPSEEK_API_KEY = "sk-your-deepseek-api-key-here"

CHAT_MODEL = "deepseek-chat"
EMBED_MODEL = "text-embedding-3-small" 

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

OPENAI_API_KEY = "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"

INDEX_DIR = "./rag_index_mnn"
INDEX_META = pathlib.Path(INDEX_DIR) / "meta.json"
INDEX_EMB = pathlib.Path(INDEX_DIR) / "embeddings.npy"
INDEX_MODEL = pathlib.Path(INDEX_DIR) / "embed_model.txt"

MAX_RADIUS_KM = 6.0     
WALK_SPEED_KMPH = 4.5   
MIN_DESC_LEN = 15
HEADERS_OSM = {"User-Agent": "nn-tour-rag/1.0 (demo)"}


def load_dataframe(path: str) -> pd.DataFrame:
    ext = pathlib.Path(path).suffix.lower()
    if ext in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
    elif ext in [".csv", ".tsv"]:
        df = pd.read_csv(path, sep="\t" if ext == ".tsv" else ",")
    else:
        raise ValueError("Поддерживаются .xlsx/.xls/.csv/.tsv")
    needed = ["id", "address", "coordinate", "description", "title"]
    for c in needed:
        if c not in df.columns:
            raise ValueError(f"Нет обязательной колонки: {c}")
    df["description"] = (df["description"].astype(str)
                         .str.replace(r"<br\s*/?>", "\n", regex=True)
                         .str.replace(r"<[^>]+>", "", regex=True)
                         .str.strip())
    coords = df["coordinate"].apply(parse_point_str)
    df["lat"] = coords.apply(lambda c: c[0] if isinstance(c, tuple) else np.nan)
    df["lon"] = coords.apply(lambda c: c[1] if isinstance(c, tuple) else np.nan)
    df = df[(df["title"].astype(str).str.len() > 0) &
            (df["description"].astype(str).str.len() >= MIN_DESC_LEN) &
            df["lat"].notna() & df["lon"].notna()].copy()
    return df.reset_index(drop=True)

def parse_point_str(s: str) -> Optional[Tuple[float, float]]:
    if not isinstance(s, str):
        return None
    s = s.strip()
    m = re.match(r"POINT\s*\(\s*([\-0-9\.]+)\s+([\-0-9\.]+)\s*\)", s, re.I)
    if m:
        lon, lat = float(m.group(1)), float(m.group(2))
        return (lat, lon)
    m2 = re.match(r"^\s*([\-0-9\.]+)\s*,\s*([\-0-9\.]+)\s*$", s)
    if m2:
        lat, lon = float(m2.group(1)), float(m2.group(2))
        return (lat, lon)
    return None

def geocode_address(addr: str) -> Optional[Tuple[float, float]]:
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": addr, "format": "json", "limit": 1},
            headers=HEADERS_OSM, timeout=15
        )
        if r.status_code == 200 and r.json():
            return (float(r.json()[0]["lat"]), float(r.json()[0]["lon"]))
    except Exception:
        pass
    return None

def ensure_user_coords(loc: str) -> Tuple[float, float]:
    pt = parse_point_str(loc)
    if pt:
        return pt
    g = geocode_address(loc)
    if g:
        return g
    return (56.3287, 44.0020)

def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    R = 6371.0
    h = (math.sin(dlat/2)**2 +
         math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(h))

def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0: return 0.0
    return float(np.dot(a, b) / denom)


def build_or_load_index(openai_client: OpenAI, df: pd.DataFrame):
    pathlib.Path(INDEX_DIR).mkdir(parents=True, exist_ok=True)
    if INDEX_EMB.exists() and INDEX_META.exists() and INDEX_MODEL.exists():
        if INDEX_MODEL.read_text(encoding="utf-8").strip() == EMBED_MODEL:
            embs = np.load(INDEX_EMB)
            meta = json.loads(INDEX_META.read_text(encoding="utf-8"))
            if len(meta) == embs.shape[0] == len(df):
                return embs, meta
    texts = [
        f"{r['title']} | {r['address']} | {r['description']}"
        for _, r in df.iterrows()
    ]
    embs = embed_texts(openai_client, texts)
    meta = []
    for _, r in df.iterrows():
        meta.append({
            "id": str(r["id"]),
            "title": r["title"],
            "address": r["address"],
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "url": str(r.get("url", "")),
            "description": r["description"][:800]
        })
    np.save(INDEX_EMB, embs)
    INDEX_META.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    INDEX_MODEL.write_text(EMBED_MODEL, encoding="utf-8")
    return embs, meta

def embed_texts(openai_client: OpenAI, strings: List[str]) -> np.ndarray:
    out = []
    B = 128
    for i in range(0, len(strings), B):
        chunk = strings[i:i+B]
        resp = openai_client.embeddings.create(model=EMBED_MODEL, input=chunk)
        out.extend([d.embedding for d in resp.data])
        time.sleep(0.2)
    return np.array(out, dtype=np.float32)

def retrieve_candidates(
    openai_client: OpenAI,
    embs: np.ndarray,
    meta: List[Dict[str, Any]],
    interests: str,
    user_coords: Tuple[float, float],
    k: int
) -> List[Dict[str, Any]]:
    q = openai_client.embeddings.create(model=EMBED_MODEL, input=[interests]).data[0].embedding
    q = np.array(q, dtype=np.float32)

    scores = []
    for i, m in enumerate(meta):
        base = cosine_sim(q, embs[i])
        d = haversine_km(user_coords, (m["lat"], m["lon"]))
        geo_bonus = 0.08 if d <= MAX_RADIUS_KM else -min(0.25, (d - MAX_RADIUS_KM) / 30.0)
        scores.append(base + geo_bonus)

    order = np.argsort(scores)[::-1][:max(k*3, k)] 
    near = [meta[i] | {"_score": float(scores[i]), "_dist_km": float(haversine_km(user_coords, (meta[i]["lat"], meta[i]["lon"])))} for i in order]
    near.sort(key=lambda x: (x["_dist_km"] > MAX_RADIUS_KM, -x["_score"]))
    return near[:k]

def nearest_neighbor_order(start: Tuple[float, float], pts: List[Tuple[float, float]]) -> List[int]:
    remaining = set(range(len(pts)))
    order, cur = [], start
    while remaining:
        j = min(remaining, key=lambda i: haversine_km(cur, pts[i]))
        order.append(j)
        cur = pts[j]
        remaining.remove(j)
    return order

def build_timeline(
    start: Tuple[float, float],
    places: List[Dict[str, Any]],
    hours_total: float
) -> List[Dict[str, Any]]:
    points = [(p["lat"], p["lon"]) for p in places]
    order = nearest_neighbor_order(start, points)
    seq = [places[i] for i in order]

    walks = []
    prev = start
    for p in seq:
        d = haversine_km(prev, (p["lat"], p["lon"]))
        walks.append(d); prev = (p["lat"], p["lon"])
    walk_min = [int(round(km / WALK_SPEED_KMPH * 60)) for km in walks]
    total_walk = sum(walk_min)

    n = len(seq)
    minutes_total = int(round(hours_total * 60))
    minutes_for_visits = max(20*n, minutes_total - total_walk)
    per_visit = max(15, int(minutes_for_visits / n))

    plan = []
    for i, p in enumerate(seq):
        if walk_min[i] > 0:
            plan.append({
                "type": "walk",
                "to": p["title"],
                "minutes": walk_min[i],
                "dist_km": round(walks[i], 2)
            })
        plan.append({
            "type": "visit",
            "title": p["title"],
            "address": p["address"],
            "minutes": per_visit,
            "url": p.get("url", ""),
            "why_hint": p["description"][:500]
        })
    return plan


def write_plan_with_llm(
    deepseek_client: OpenAI,
    interests: str,
    hours: float,
    user_location_str: str,
    user_coords: Tuple[float, float],
    selected_places: List[Dict[str, Any]],
    timeline: List[Dict[str, Any]]
) -> str:
    sys = (
        "Ты — локальный гид по Нижнему Новгороду. Сформируй краткий, дружелюбный план прогулки: "
        "3–5 мест, для каждого — 1–2 фразы «почему сюда», предложи порядок и таймлайн по минутам. "
        "Строго используй ТОЛЬКО переданные места; ничего не придумывай. Пиши по-русски, списками."
    )
    payload = {
        "interests": interests,
        "hours_available": hours,
        "user_location_input": user_location_str,
        "user_coords": user_coords,
        "places": [
            {
                "title": p["title"], "address": p["address"],
                "lat": p["lat"], "lon": p["lon"], "url": p.get("url", ""),
                "why": p["description"][:700]
            } for p in selected_places
        ],
        "timeline_raw": timeline
    }
    resp = deepseek_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ]
    )
    return resp.choices[0].message.content.strip()


def decide_k(hours: float) -> int:
    if hours <= 1.5: return 3
    if hours <= 3.0: return 4
    return 5

