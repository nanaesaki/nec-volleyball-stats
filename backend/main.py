import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from parser_b import parse_b_form_from_source

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MATCHES_FILE = DATA_DIR / "matches.json"

TARGET_TEAM = "NECレッドロケッツ川崎"
TARGET_PLAYER_NAME = "廣田 あい"

app = FastAPI(title="SV.LEAGUE Volleyball Analytics API", version="1.0.0")

# GitHub Pages やローカルHTMLからのアクセスを許可
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MatchIngestRequest(BaseModel):
    form_a_url: Optional[str] = None
    form_b_url: Optional[str] = None
    live_score_url: Optional[str] = None
    comment_home: Optional[str] = ""
    comment_away: Optional[str] = ""


def load_matches() -> list[dict]:
    if not MATCHES_FILE.exists():
        return []
    with open(MATCHES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_matches(matches: list[dict]) -> None:
    with open(MATCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)


def extract_match_id_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/match/detail/(\d+)", url)
    return m.group(1) if m else None


async def fetch_live_score_summary(live_url: Optional[str]) -> dict:
    """
    Live Score URLからタイトルや取得可否を確認する軽量フェッチャー
    （SPA等でJS描画が必要な場合は取得ステータスとURLを安全に保持）
    """
    if not live_url:
        return {"url": None, "fetched": False, "title": None}

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(live_url, headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(res.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else None
            return {
                "url": live_url,
                "fetched": res.status_code == 200,
                "status_code": res.status_code,
                "title": title,
            }
    except Exception as e:
        return {"url": live_url, "fetched": False, "error": str(e)}


def detect_target_side(parsed_b: dict) -> str:
    """ホーム/アウェーのどちらがNECレッドロケッツ川崎かを判定"""
    home_players = parsed_b.get("home_team", {}).get("players", [])
    away_players = parsed_b.get("away_team", {}).get("players", [])
    if any(TARGET_PLAYER_NAME in p.get("name", "") for p in home_players):
        return "home"
    if any(TARGET_PLAYER_NAME in p.get("name", "") for p in away_players):
        return "away"
    return "away"


def build_dashboard_payload(matches: list[dict]) -> dict:
    """蓄積された全試合からダッシュボード表示用の集計データを生成"""
    hirota_history = []
    team_history = []

    for m in matches:
        b_data = m.get("parsed_b", {})
        side = m.get("target_side", "away")
        nec_block = b_data.get("away_team" if side == "away" else "home_team", {})
        opp_block = b_data.get("home_team" if side == "away" else "away_team", {})

        nec_total = nec_block.get("team_total", {})
        opp_total = opp_block.get("team_total", {})

        hirota_stat = next(
            (p for p in nec_block.get("players", []) if TARGET_PLAYER_NAME in p.get("name", "")),
            None,
        )

        match_label = m.get("meta", {}).get("date") or f"Match #{m.get('id')}"

        if hirota_stat:
            total_pts = (hirota_stat.get("attack_pts") or 0) + (hirota_stat.get("block_pts") or 0) + (hirota_stat.get("serve_pts") or 0)
            hirota_history.append({
                "match_id": m.get("id"),
                "date": match_label,
                "total_pts": total_pts,
                "attack_pts": hirota_stat.get("attack_pts", 0),
                "attack_att": hirota_stat.get("attack_att", 0),
                "attack_pct": hirota_stat.get("attack_pct"),
                "block_pts": hirota_stat.get("block_pts", 0),
                "serve_pct": hirota_stat.get("serve_pct"),
                "rec_att": hirota_stat.get("rec_att", 0),
                "rec_pct": hirota_stat.get("rec_pct"),
            })

        team_history.append({
            "match_id": m.get("id"),
            "date": match_label,
            "nec_attack_pct": nec_total.get("attack_pct"),
            "opp_attack_pct": opp_total.get("attack_pct"),
            "nec_block_pts": nec_total.get("block_pts", 0),
            "opp_block_pts": opp_total.get("block_pts", 0),
            "nec_serve_pct": nec_total.get("serve_pct"),
            "opp_serve_pct": opp_total.get("serve_pct"),
            "nec_rec_pct": nec_total.get("rec_pct"),
            "opp_rec_pct": opp_total.get("rec_pct"),
        })

    latest_match = matches[-1] if matches else None
    latest_hirota = hirota_history[-1] if hirota_history else None

    return {
        "target_team": TARGET_TEAM,
        "target_player": TARGET_PLAYER_NAME,
        "total_matches": len(matches),
        "latest_match": latest_match,
        "latest_hirota": latest_hirota,
        "hirota_history": hirota_history,
        "team_history": team_history,
        "matches": matches,
    }


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "svleague-analytics-api"}


@app.get("/api/dashboard")
def get_dashboard():
    matches = load_matches()
    return build_dashboard_payload(matches)


@app.post("/api/matches/ingest")
async def ingest_match(req: MatchIngestRequest):
    if not req.form_b_url and not req.live_score_url:
        raise HTTPException(status_code=400, detail="帳票BのURLまたはLive ScoreのURLを指定してください。")

    parsed_b = {}
    if req.form_b_url:
        try:
            parsed_b = parse_b_form_from_source(req.form_b_url)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"帳票Bの解析に失敗しました: {e}")

    live_info = await fetch_live_score_summary(req.live_score_url)
    live_match_id = extract_match_id_from_url(req.live_score_url)

    meta = parsed_b.get("meta", {})
    match_no = meta.get("match_no")
    record_id = str(match_no or live_match_id or int(datetime.now(timezone.utc).timestamp()))

    side = detect_target_side(parsed_b) if parsed_b else "away"

    match_record = {
        "id": record_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "target_side": side,
        "sources": {
            "form_a_url": req.form_a_url,
            "form_b_url": req.form_b_url,
            "live_score": live_info,
        },
        "comments": {
            "home": req.comment_home or "",
            "away": req.comment_away or "",
        },
        "parsed_b": parsed_b,
    }

    matches = load_matches()
    # 同一試合IDがあれば上書き、なければ追加
    matches = [m for m in matches if m.get("id") != record_id]
    matches.append(match_record)
    save_matches(matches)

    return {
        "status": "saved",
        "match_id": record_id,
        "dashboard": build_dashboard_payload(matches),
    }
