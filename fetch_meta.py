"""
GBFキャラメタデータ（属性・得意武器・種族）をgbf.wikiから取得してDBに保存

使い方:
  python fetch_meta.py

再取得（更新）したい場合も同じコマンドで上書きされます。
"""
import cloudscraper
import sqlite3
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "skinsize.db")

API    = "https://gbf.wiki/api.php"
LIMIT  = 500
scraper = cloudscraper.create_scraper()

# 英語 → 日本語
ELEMENT = {
    "Fire": "火", "Water": "水", "Earth": "土",
    "Wind": "風", "Light": "光", "Dark": "闇",
}
WEAPON = {
    "Sabre": "剣", "Sword": "剣",  # wiki は Sabre を使用
    "Dagger": "短剣", "Spear": "槍", "Axe": "斧",
    "Staff": "杖", "Bow": "弓", "Melee": "格闘", "Harp": "楽器",
    "Katana": "刀", "Gun": "銃",
}
RACE = {
    "Human":  "ヒューマン", "Erune":  "エルーン", "Draph":  "ドラフ",
    "Harvin": "ハーヴィン", "Primal": "星晶獣",   "Unknown": "不明",
    "Other":  "その他",
}
GENDER = {
    "m": "男", "male":   "男",
    "f": "女", "female": "女",
    "o": "不詳", "other": "不詳",
    "mf": "不詳", "mo": "不詳", "fo": "不詳",
}
SERIES = {
    "summer":      "水着",
    "yukata":      "浴衣",
    "valentine":   "バレンタイン",
    "halloween":   "ハロウィン",
    "holiday":     "クリスマス",
    "formal":      "ドレスアップ",
    "12generals":  "十二神将",
    "grand":       "リミテッド",
    "fantasy":     "アナザー",
    "tie-in":      "コラボ",
    "eternals":    "十天衆",
    "evokers":     "十賢者",
    "4saints":     "四聖",
}


def fetch_wiki():
    """gbf.wiki から全キャラデータをページングして取得"""
    results, offset = [], 0
    while True:
        resp = scraper.get(API, params={
            "action": "cargoquery",
            "tables": "characters",
            "fields": "_pageName=name,id,element,weapon,race,series,gender,rarity",
            "limit": LIMIT,
            "offset": offset,
            "format": "json",
        }, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("cargoquery", [])
        if not items:
            break
        results.extend(item["title"] for item in items)
        print(f"  取得中... {len(results)} 件", end="\r")
        if len(items) < LIMIT:
            break
        offset += LIMIT
        time.sleep(0.3)
    print()
    return results


def init_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS character_meta (
            char_id TEXT PRIMARY KEY,
            element TEXT NOT NULL DEFAULT '',
            weapon  TEXT NOT NULL DEFAULT '',
            race    TEXT NOT NULL DEFAULT '',
            series  TEXT NOT NULL DEFAULT '',
            gender  TEXT NOT NULL DEFAULT ''
        )
    """)
    # 既存テーブルへの列追加（初回のみ）
    for col in ("series", "gender", "rarity"):
        try:
            conn.execute(f"ALTER TABLE character_meta ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
    conn.commit()
    conn.close()


def match_and_save(records):
    """wiki レコードを全件 character_meta に保存（skins の有無を問わない）"""
    conn = sqlite3.connect(DB_PATH)

    saved = 0
    for r in records:
        wiki_id = (r.get("id") or "").strip()
        element  = ELEMENT.get((r.get("element") or "").strip(), "")
        weapon = ",".join(
            WEAPON.get(w.strip(), w.strip())
            for w in (r.get("weapon") or "").split(",")
            if w.strip()
        )
        series = ",".join(
            SERIES.get(s.strip(), s.strip())
            for s in (r.get("series") or "").split(",")
            if s.strip()
        )
        gender   = GENDER.get((r.get("gender")  or "").strip().lower(), "")
        rarity   = (r.get("rarity") or "").strip()

        race_raw = (r.get("race") or "")
        race = ",".join(
            RACE.get(rc.strip(), rc.strip())
            for rc in race_raw.replace("|", ",").split(",")
            if rc.strip()
        )

        if not element or len(wiki_id) != 10 or not wiki_id.isdigit():
            continue

        conn.execute("""
            INSERT INTO character_meta (char_id, element, weapon, race, series, gender, rarity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(char_id) DO UPDATE SET
                element = excluded.element,
                weapon  = excluded.weapon,
                race    = excluded.race,
                series  = excluded.series,
                gender  = excluded.gender,
                rarity  = excluded.rarity
        """, (wiki_id, element, weapon, race, series, gender, rarity))
        saved += 1

    conn.commit()
    conn.close()
    return saved


def run_fetch(db_path=None):
    """アプリから呼び出し可能。成功時 (saved, total) を返す"""
    global DB_PATH
    if db_path:
        DB_PATH = db_path
    records = fetch_wiki()
    init_table()
    saved = match_and_save(records)
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM character_meta").fetchone()[0]
    conn.close()
    return saved, total


def main():
    print("gbf.wiki からデータ取得中...")
    saved, total = run_fetch()
    print(f"DBに保存: {saved} 件 / character_meta 合計: {total} 件")


if __name__ == "__main__":
    main()
