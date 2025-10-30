import os
import logging
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

logging.basicConfig(
    filename='logs/bot.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger("bot")

DEEPSEEK_API_KEY = "sk-your-deepseek-api-key-here"

CHAT_MODEL = "deepseek-chat"
EMBED_MODEL = "text-embedding-3-small" 

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

OPENAI_API_KEY = "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"

DATASET_PATH = "./cultural_objects_mnn.xlsx"

INDEX_DIR = "./rag_index_mnn"
INDEX_META = pathlib.Path(INDEX_DIR) / "meta.json"
INDEX_EMB = pathlib.Path(INDEX_DIR) / "embeddings.npy"
INDEX_MODEL = pathlib.Path(INDEX_DIR) / "embed_model.txt"

MAX_RADIUS_KM = 6.0
WALK_SPEED_KMPH = 4.5
HEADERS_OSM = {"User-Agent": "nn-tour-rag/1.0 (demo)"}

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

def load_existing_rag():
    if not INDEX_EMB.exists() or not INDEX_META.exists():
        raise FileNotFoundError("RAG индекс не найден! Сначала запустите nn_tour_rag.py")
    
    logger.info("Загружаю существующий RAG индекс...")
    embs = np.load(INDEX_EMB)
    meta = json.loads(INDEX_META.read_text(encoding="utf-8"))
    logger.info(f"Загружено {len(meta)} объектов из RAG индекса")
    return embs, meta

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
        "Предлагай места в таком порядке в каком нужно его проходить, тоесть сразу оптимизируй его, чтобы пользователь не проходил лишнее растояние"
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

def create_tour_plan(interests: str, hours: float, location: str) -> str:
    embs, meta = load_existing_rag()
    
    openai_client = OpenAI(api_key=OPENAI_API_KEY)  
    deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL) 
    
    user_coords = ensure_user_coords(location)
    
    k = decide_k(hours)
    candidates = retrieve_candidates(openai_client, embs, meta, interests, user_coords, k)
    
    timeline = build_timeline(user_coords, candidates, hours)
    
    plan_text = write_plan_with_llm(
        deepseek_client, interests, hours, location, user_coords, candidates, timeline
    )
    
    return plan_text