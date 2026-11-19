import io
import re
import unicodedata
import urllib.request
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer

B_FORM_COLUMNS = [
    ("number", 40, 56),
    ("player_name", 56, 165),
    ("sets_played", 165, 178),
    ("set_marks", 178, 225),
    ("attack_att", 225, 240),
    ("attack_pts", 240, 255),
    ("attack_err", 255, 270),
    ("attack_pct", 270, 290),
    ("attack_per_set", 290, 310),
    ("back_att", 310, 325),
    ("back_pts", 325, 340),
    ("back_err", 340, 355),
    ("back_pct", 355, 375),
    ("block_pts", 375, 390),
    ("block_per_set", 390, 410),
    ("serve_att", 410, 425),
    ("serve_pts", 425, 440),
    ("serve_err", 440, 455),
    ("serve_eff", 455, 470),
    ("serve_pct", 470, 490),
    ("rec_att", 490, 505),
    ("rec_exc", 505, 520),
    ("rec_good", 520, 535),
    ("rec_pct", 535, 555),
]


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def to_number(val: str, is_float: bool = False):
    val = val.replace(" ", "").strip()
    if not val or val == "-":
        return None if is_float else 0
    try:
        return float(val) if is_float else int(val)
    except ValueError:
        return None if is_float else 0


def parse_b_form_from_source(pdf_source: str | bytes) -> dict:
    if isinstance(pdf_source, str) and pdf_source.startswith(("http://", "https://")):
        req = urllib.request.Request(pdf_source, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as res:
            stream = io.BytesIO(res.read())
    elif isinstance(pdf_source, bytes):
        stream = io.BytesIO(pdf_source)
    else:
        stream = pdf_source

    items = []
    for page_layout in extract_pages(stream):
        for element in page_layout:
            if isinstance(element, LTTextContainer):
                for line in element:
                    txt = normalize_text(line.get_text())
                    if txt:
                        items.append((line.bbox[0], line.bbox[1], line.bbox[2], line.bbox[3], txt))

    rows = []
    for x0, y0, x1, y1, txt in sorted(items, key=lambda x: (-x[1], x[0])):
        placed = False
        for r in rows:
            if abs(r["y"] - y0) < 2.5:
                r["cells"].append((x0, x1, txt))
                placed = True
                break
        if not placed:
            rows.append({"y": y0, "cells": [(x0, x1, txt)]})

    meta = {"match_no": None, "date": None, "venue": None}
    for r in rows:
        if r["y"] > 750:
            line_str = " ".join(c[2] for c in sorted(r["cells"], key=lambda x: x[0]))
            m_no = re.search(r"Match No\.\s*(\d+)", line_str)
            m_date = re.search(r"開催日\s*(\d{4}/\d{2}/\d{2})", line_str)
            m_venue = re.search(r"会場[:：]\s*(\S+)", line_str)
            if m_no:
                meta["match_no"] = int(m_no.group(1))
            if m_date:
                meta["date"] = m_date.group(1)
            if m_venue:
                meta["venue"] = m_venue.group(1)

    total_ys = sorted(
        [r["y"] for r in rows if any("チーム合計" in c[2] for c in r["cells"])],
        reverse=True,
    )
    home_total_y = total_ys[0] if len(total_ys) >= 1 else 302.0
    away_total_y = total_ys[1] if len(total_ys) >= 2 else 117.0

    def extract_team(y_min: float, y_max: float) -> dict:
        players = []
        team_total = {}
        for r in rows:
            if y_min <= r["y"] <= y_max:
                raw = {col[0]: "" for col in B_FORM_COLUMNS}
                for x0, x1, txt in sorted(r["cells"], key=lambda c: c[0]):
                    xc = (x0 + x1) / 2
                    for col_name, xmin, xmax in B_FORM_COLUMNS:
                        if xmin <= xc < xmax:
                            raw[col_name] = (raw[col_name] + " " + txt).strip()
                            break

                name = raw["player_name"]
                if not name or "出場セット" in name or name == "セット":
                    continue

                record = {
                    "number": to_number(raw["number"]) if raw["number"].isdigit() else None,
                    "name": name,
                    "is_libero": name.startswith("L "),
                    "sets_played": to_number(raw["sets_played"]),
                    "attack_att": to_number(raw["attack_att"]),
                    "attack_pts": to_number(raw["attack_pts"]),
                    "attack_err": to_number(raw["attack_err"]),
                    "attack_pct": to_number(raw["attack_pct"], is_float=True),
                    "back_att": to_number(raw["back_att"]),
                    "back_pts": to_number(raw["back_pts"]),
                    "back_pct": to_number(raw["back_pct"], is_float=True),
                    "block_pts": to_number(raw["block_pts"]),
                    "serve_att": to_number(raw["serve_att"]),
                    "serve_pts": to_number(raw["serve_pts"]),
                    "serve_err": to_number(raw["serve_err"]),
                    "serve_eff": to_number(raw["serve_eff"]),
                    "serve_pct": to_number(raw["serve_pct"], is_float=True),
                    "rec_att": to_number(raw["rec_att"]),
                    "rec_exc": to_number(raw["rec_exc"]),
                    "rec_good": to_number(raw["rec_good"]),
                    "rec_pct": to_number(raw["rec_pct"], is_float=True),
                }

                if "チーム合計" in name:
                    team_total = record
                elif record["number"] is not None:
                    players.append(record)

        sum_attack = sum(p["attack_pts"] for p in players)
        sum_block = sum(p["block_pts"] for p in players)
        return {
            "players": players,
            "team_total": team_total,
            "validation": {
                "attack_pts_match": sum_attack == team_total.get("attack_pts"),
                "block_pts_match": sum_block == team_total.get("block_pts"),
            },
        }

    return {
        "meta": meta,
        "home_team": extract_team(home_total_y - 3, home_total_y + 150),
        "away_team": extract_team(away_total_y - 3, away_total_y + 135),
    }
