import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from parser_b import parse_b_form_from_source

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "matches.json"


def extract_live_match_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"/match/detail/(\d+)", url)
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url).query)
    return qs.get("matchId", [None])[0]


def load_matches() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    raw = DATA_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return json.loads(raw)


def save_matches(matches: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(matches, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    form_b_url = os.environ.get("FORM_B_URL", "").strip()
    form_a_url = os.environ.get("FORM_A_URL", "").strip()
    live_score_url = os.environ.get("LIVE_SCORE_URL", "").strip()
    comment_home = os.environ.get("COMMENT_HOME", "").strip()
    comment_away = os.environ.get("COMMENT_AWAY", "").strip()

    if not form_b_url and not live_score_url and not comment_home and not comment_away:
        raise ValueError("少なくとも1つの入力項目を指定してください。")

    parsed_b = {}
    if form_b_url:
        parsed_b = parse_b_form_from_source(form_b_url)

    meta = parsed_b.get("meta", {})
    match_no = meta.get("match_no")
    live_id = extract_live_match_id(live_score_url)
    record_id = str(match_no or live_id or int(datetime.now(timezone.utc).timestamp()))

    record = {
        "id": record_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "sources": {
            "form_a_url": form_a_url or None,
            "form_b_url": form_b_url or None,
            "live_score_url": live_score_url or None,
        },
        "comments": {
            "home": comment_home,
            "away": comment_away,
        },
        "parsed_b": parsed_b,
    }

    matches = load_matches()
    matches = [m for m in matches if m.get("id") != record_id]
    matches.append(record)
    # 試合日（またはID）順に並べる
    matches.sort(key=lambda x: (x.get("meta", {}).get("date") or "", str(x.get("id", ""))))
    save_matches(matches)
    print(f"Saved match_id={record_id}. Total matches={len(matches)}")


if __name__ == "__main__":
    main()
