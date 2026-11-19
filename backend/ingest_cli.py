import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from parser_b import parse_b_form_from_source

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "matches.json"


def extract_id_from_url(url: str) -> str | None:
    if not url:
        return None
    # /match/form/?id=37492&sheet=a 形式や ?matchId=... 形式に対応
    qs = parse_qs(urlparse(url).query)
    if "id" in qs and qs["id"]:
        return qs["id"][0]
    if "matchId" in qs and qs["matchId"]:
        return qs["matchId"][0]
    m = re.search(r"/match/(?:detail|form)/(\d+)", url)
    if m:
        return m.group(1)
    return None


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

    if not any([form_b_url, form_a_url, live_score_url, comment_home, comment_away]):
        raise ValueError("少なくとも1つの入力項目を指定してください。")

    parsed_b = {}
    if form_b_url and not form_b_url.startswith("file://"):
        try:
            parsed_b = parse_b_form_from_source(form_b_url)
        except Exception as e:
            print(f"[WARN] 帳票BのPDF解析をスキップしました: {e}")
            parsed_b = {
                "parse_error": str(e),
                "note": "URLがWebページ(HTML)またはアクセス制限対象のため、PDF直接解析をスキップしました。",
            }
    elif form_b_url.startswith("file://"):
        print("[WARN] file:// パスはクラウド上から参照できないためスキップしました。")
        parsed_b = {
            "parse_error": "local file:// path is not accessible from GitHub Actions",
        }

    meta = parsed_b.get("meta", {}) if isinstance(parsed_b, dict) else {}
    match_no = meta.get("match_no")
    url_id = (
        extract_id_from_url(live_score_url)
        or extract_id_from_url(form_b_url)
        or extract_id_from_url(form_a_url)
    )
    record_id = str(match_no or url_id or int(datetime.now(timezone.utc).timestamp()))

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
    matches.sort(key=lambda x: (str(x.get("meta", {}).get("date") or ""), str(x.get("id", ""))))
    save_matches(matches)
    print(f"Saved match_id={record_id}. Total matches={len(matches)}")


if __name__ == "__main__":
    main()
