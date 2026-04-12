"""
GBFスキンサイズ収集 mitmproxyアドオン

使い方:
  mitmdump -s collector.py
  または既存のdn.pyと同時に:
  mitmdump -s dn.py -s collector.py
"""

import re
import json
import sqlite3
import os
import io
from PIL import Image
from mitmproxy import http

# パス設定
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "skinsize.db")
IMG_DIR = os.path.join(BASE_DIR, "static", "images")

# URLマッチング用の正規表現
BASE     = r"prd-game-a-granbluefantasy\.akamaized\.net/assets/img_low/sp/"
PATTERNS = {
    # SDスプライト: npc_{ID}_{スキン}.png or npc_{ID}_{スキン}_{シート}.png
    "sd": re.compile(
        BASE + r"cjs/npc_(\d+)_(\d+)(?:_([a-z]))?\.png"
    ),
    # 編成画面: quest/skin/{ID}_{スキン}[_{差分番号}]_s{N}.jpg
    "quest": re.compile(
        BASE + r"assets/npc/quest/(?:skin/)?(\d+)_(\d+)(?:_(\d+))?(?:_s(\d+))?\.jpg"
    ),
    # バトル中: {ID}_{スキン}[_{差分番号}].jpg
    "raid": re.compile(
        BASE + r"assets/npc/raid_normal/(\d+)_(\d+)(?:_(\d+))?\.jpg"
    ),
    # CJS JSファイル（キャラ本体アニメ）※版数ハッシュ部分は\d+で吸収
    "cjs_npc": re.compile(
        r"prd-game-a-granbluefantasy\.akamaized\.net/assets/\d+/js/cjs/npc_(\d+)_(\d+)(?:_s\d+)?(?:_([a-z]))?\.js"
    ),
    # CJS phit JSファイル（攻撃エフェクト）: phit_{ID}.js or phit_{ID}_{スキン}.js
    "cjs_phit": re.compile(
        r"prd-game-a-granbluefantasy\.akamaized\.net/assets/\d+/js/cjs/phit_(3\d+)(?:_(\d+))?\.js"
    ),
    # phit スプライトPNG（主人公=1040...は除外、キャラ=3...のみ）
    "phit_png": re.compile(
        BASE + r"cjs/phit_(3\d+)(?:_(\d+))?\.png"
    ),
}

# キャラ名取得対象のURLパターン
RAID_START = re.compile(r"game\.granbluefantasy\.jp/rest/raid/start\.json")

# 現在のバトルに参加している非主人公キャラのchar_idセット
_active_char_ids: set = set()


def init_dirs():
    """画像保存用ディレクトリを初期化"""
    for rtype in ("sd", "quest", "raid"):
        os.makedirs(os.path.join(IMG_DIR, rtype), exist_ok=True)


def to_webp_filename(filename):
    """拡張子を.webpに変換"""
    return re.sub(r'\.(png|jpg|jpeg)$', '.webp', filename, flags=re.IGNORECASE)


def save_image(rtype, filename, content, char_id=None, skin_num=None, sheet=None):
    """画像ファイルをWebPに変換して保存（既存なら上書きしない）"""
    webp_filename = to_webp_filename(filename)

    # quest: sheetあり版が来たら対応するsheetなし版ファイルを削除
    if rtype == "quest" and sheet and char_id and skin_num:
        for old_fname in (f"{char_id}_{skin_num}.webp", f"{char_id}_{skin_num}.jpg"):
            old_path = os.path.join(IMG_DIR, rtype, old_fname)
            if os.path.exists(old_path):
                os.remove(old_path)

    path = os.path.join(IMG_DIR, rtype, webp_filename)
    if not os.path.exists(path):
        try:
            img = Image.open(io.BytesIO(content))
            img.save(path, "WEBP", quality=85, method=6)
        except Exception as e:
            print(f"[skinsize] WebP変換失敗 ({filename}): {e}")
            with open(os.path.join(IMG_DIR, rtype, filename), "wb") as f:
                f.write(content)
            return filename  # 変換失敗時は元のファイル名を返す
    return webp_filename


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """DBとテーブルを初期化"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            char_id TEXT NOT NULL,
            skin_num TEXT NOT NULL,
            type TEXT NOT NULL,
            sheet TEXT,
            filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            UNIQUE(char_id, skin_num, type, sheet)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            char_id TEXT NOT NULL,
            skin_num TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (char_id, skin_num)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS js_exec (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            char_id     TEXT    NOT NULL,
            skin_num    TEXT    NOT NULL,
            type        TEXT    NOT NULL DEFAULT 'npc',
            exec_time_us INTEGER NOT NULL,
            trace_file  TEXT
        )
    """)
    conn.commit()
    conn.close()


def auto_fix_sheets():
    """起動時に sheet='' だがファイル名に差分番号が含まれるレコードを自動補正"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, type, filename FROM skins WHERE sheet = ''"
    ).fetchall()
    fixed = 0
    for row in rows:
        rid = row[0]
        rtype = row[1]
        filename = row[2]

        sheet = ""
        if rtype in ("quest", "raid"):
            # quest: {id}_{skin}_{variant}_s{N}.jpg or {id}_{skin}_{variant}.jpg
            # raid:  {id}_{skin}_{variant}.jpg
            base = filename.rsplit(".", 1)[0]  # 拡張子除去
            parts = base.split("_")
            # parts例: ["3040512000","01","101"] or ["3040512000","01","101","s6"]
            if rtype == "quest":
                # _s{N} が末尾にあるか
                sn_part = None
                variant_part = None
                if parts and re.match(r'^s\d+$', parts[-1]):
                    sn_part = parts[-1]
                    remaining = parts[:-1]
                else:
                    remaining = parts
                # remaining末尾が3桁以上の数字なら variant
                if remaining and re.match(r'^\d{3,}$', remaining[-1]):
                    variant_part = remaining[-1]
                if variant_part and sn_part:
                    sheet = f"{variant_part}_{sn_part}"
                elif sn_part:
                    sheet = sn_part
                elif variant_part:
                    sheet = variant_part
            elif rtype == "raid":
                # parts末尾が3桁以上の数字なら variant
                if parts and re.match(r'^\d{3,}$', parts[-1]):
                    sheet = parts[-1]

        if sheet:
            try:
                conn.execute(
                    "UPDATE skins SET sheet = ? WHERE id = ?",
                    (sheet, rid)
                )
                fixed += 1
            except sqlite3.IntegrityError:
                # UNIQUE制約違反（すでに同じキーで存在）→ 古いレコードを削除
                conn.execute("DELETE FROM skins WHERE id = ?", (rid,))
                fixed += 1

    if fixed:
        conn.commit()
        print(f"[skinsize] auto_fix_sheets: {fixed} 件修正")
    conn.close()


def save_record(char_id, skin_num, rtype, sheet, filename, size_bytes):
    """スキンサイズレコードをDBに保存（最大10サンプルまで蓄積）"""
    conn = get_conn()

    # quest: sheetあり版が来たらsheetなし版を削除（差し替え）
    if rtype == "quest" and sheet:
        conn.execute(
            "DELETE FROM skins WHERE char_id=? AND skin_num=? AND type='quest' AND sheet=''",
            (char_id, skin_num)
        )

    exists = conn.execute(
        "SELECT 1 FROM skins WHERE filename = ? AND type = ? LIMIT 1", (filename, rtype)
    ).fetchone()
    if not exists:
        conn.execute("""
            INSERT INTO skins (char_id, skin_num, type, sheet, filename, size_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (char_id, skin_num, rtype, sheet, filename, size_bytes))
        conn.commit()
    conn.close()


def save_character(char_id, skin_num, name):
    """キャラ名をDBに保存（既存なら更新）"""
    conn = get_conn()
    conn.execute("""
        INSERT INTO characters (char_id, skin_num, name)
        VALUES (?, ?, ?)
        ON CONFLICT(char_id, skin_num) DO UPDATE SET
            name = excluded.name
    """, (char_id, skin_num, name))
    conn.commit()
    conn.close()


def parse_raid_start(flow):
    """raid/start.jsonからキャラ名を抽出して保存"""
    global _active_char_ids
    try:
        data = json.loads(flow.response.content)
        player = data.get("player", {})
        params = player.get("param", [])
        _active_char_ids = set()
        for p in params:
            name = p.get("name", "")
            cjs = p.get("cjs", "")
            # cjs例: "npc_3040115000_01"
            m = re.match(r"npc_(\d+)_(\d+)", cjs)
            if m and name:
                char_id = m.group(1)
                skin_num = m.group(2)
                _active_char_ids.add(char_id)
                save_character(char_id, skin_num, name)
                print(f"[skinsize] キャラ名: {name} ({char_id} スキン{skin_num})")
        print(f"[skinsize] 編成キャラ: {_active_char_ids}")
    except Exception:
        pass


def response(flow: http.HTTPFlow):
    """mitmproxyのレスポンスフック"""
    if flow.response is None:
        return

    url = flow.request.pretty_url

    # キャラ名取得
    if RAID_START.search(url):
        parse_raid_start(flow)
        return

    # 画像サイズ取得
    for rtype, pattern in PATTERNS.items():
        m = pattern.search(url)
        if m:
            char_id = m.group(1)
            if rtype in ("cjs_phit", "phit_png"):
                # group(2)はスキン番号（省略時はNone→""=デフォルトphit）
                skin_num = m.group(2) or ""
                sheet = ""
                filename = url.split("?")[0].split("/")[-1]
                size_bytes = len(flow.response.content)
                save_record(char_id, skin_num, rtype, sheet, filename, size_bytes)
                # phit IDが編成キャラと異なる場合、編成キャラにも紐付け
                if _active_char_ids and char_id not in _active_char_ids:
                    for active_id in _active_char_ids:
                        save_record(active_id, skin_num, rtype, sheet, filename, size_bytes)
                        print(f"[skinsize] phit alias: {active_id} ← {char_id} ({filename})")
                print(f"[skinsize] {rtype}: {filename} ({size_bytes:,} bytes)")
                break
            else:
                skin_num = m.group(2)
                if rtype == "sd":
                    sheet = m.group(3) or ""
                elif rtype == "quest":
                    variant = m.group(3)  # 差分番号（101, 102など）
                    sn = m.group(4)       # sN番号
                    if variant and sn:
                        sheet = f"{variant}_s{sn}"
                    elif sn:
                        sheet = f"s{sn}"
                    elif variant:
                        sheet = variant
                    else:
                        sheet = ""
                elif rtype == "raid":
                    sheet = m.group(3) if m.group(3) else ""
                elif rtype == "cjs_npc":
                    sheet = m.group(3) or ""
                else:
                    sheet = ""
            filename = url.split("?")[0].split("/")[-1]
            size_bytes = len(flow.response.content)

            if rtype not in ("cjs_npc", "cjs_phit", "phit_png"):
                saved_filename = save_image(rtype, filename, flow.response.content, char_id=char_id, skin_num=skin_num, sheet=sheet)
                save_record(char_id, skin_num, rtype, sheet, saved_filename, size_bytes)
            else:
                save_record(char_id, skin_num, rtype, sheet, filename, size_bytes)
            print(f"[skinsize] {rtype}: {filename} ({size_bytes:,} bytes)")
            break


# 起動時に初期化
init_dirs()
init_db()
auto_fix_sheets()
