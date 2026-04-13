import sys
import re
import sqlite3
import os
import json
import base64
import threading
import io
from collections import Counter
from flask import Flask, render_template, redirect, url_for, request, flash

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from PIL import Image as _Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

sys.stdout.reconfigure(encoding='utf-8')

app = Flask(__name__)
app.secret_key = "gbf_skinsize"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "skinsize.db")
IMG_DIR  = os.path.join(BASE_DIR, "static", "images")
CFG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "section_order": ["sd", "quest", "raid"],
    "show_cjs": False,
    "custom_thumbs": {},
}

def load_config():
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        # 不足キーをデフォルトで補完
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(DEFAULT_CONFIG)


def img_data_uri(rtype, filename):
    """画像ファイルをbase64 data URIに変換。失敗時はNone"""
    path = os.path.join(IMG_DIR, rtype, filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        ext = filename.rsplit(".", 1)[-1].lower()
        mime = "image/webp" if ext == "webp" else f"image/{ext}"
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    except Exception:
        return None

def save_config(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_db():
    """既存DBへのスキーマ追加（初回のみ実行）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("ALTER TABLE js_exec ADD COLUMN type TEXT NOT NULL DEFAULT 'npc'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列がすでに存在する
    conn.execute("""
        CREATE TABLE IF NOT EXISTS character_meta (
            char_id TEXT PRIMARY KEY,
            element TEXT NOT NULL DEFAULT '',
            weapon  TEXT NOT NULL DEFAULT '',
            race    TEXT NOT NULL DEFAULT '',
            series  TEXT NOT NULL DEFAULT ''
        )
    """)
    for col in ("series", "gender", "rarity"):
        try:
            conn.execute(f"ALTER TABLE character_meta ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS phit_chars (
            char_id  TEXT NOT NULL,
            skin_num TEXT NOT NULL DEFAULT '',
            phit_id  TEXT NOT NULL,
            PRIMARY KEY (char_id, skin_num, phit_id)
        )
    """)
    conn.commit()
    conn.close()


@app.route("/")
def index():
    conn = get_db()

    # 全char_idとその名前・スキン数を取得
    rows = conn.execute("""
        SELECT
            s.char_id,
            MAX(c.name) AS name,
            COUNT(DISTINCT s.skin_num) AS skin_count
        FROM skins s
        LEFT JOIN characters c ON s.char_id = c.char_id AND s.skin_num = c.skin_num
        GROUP BY s.char_id
        ORDER BY s.char_id
    """).fetchall()

    # 名前でグループ化（名前なしは未検証として除外）
    groups = {}
    for row in rows:
        if not row["name"]:
            continue
        key = row["name"]
        if key not in groups:
            groups[key] = {"name": row["name"], "char_ids": [], "skin_count": 0, "thumb": None}
        groups[key]["char_ids"].append(row["char_id"])
        groups[key]["skin_count"] += row["skin_count"]

    # サムネイル（quest画像）を取得: skin_num最小 × 最多属性(sN)
    chars = []
    sn_pat = re.compile(r'_s(\d+)\.')
    for key, g in groups.items():
        for char_id in g["char_ids"]:
            quest_files = conn.execute("""
                SELECT DISTINCT filename, skin_num, sheet FROM skins
                WHERE char_id = ? AND type = 'quest'
            """, (char_id,)).fetchall()
            if not quest_files:
                continue
            # 最多属性(sN)を集計
            sn_freq = Counter()
            for r in quest_files:
                m = sn_pat.search(r["filename"])
                if m:
                    sn_freq[int(m.group(1))] += 1
            most_common_sn = sn_freq.most_common(1)[0][0] if sn_freq else None
            # skin_num昇順 → オリジナル(差分なし)→101→102順 でソート
            def thumb_sort(r):
                sheet = r["sheet"] or ""
                # 差分番号を取り出す（'101_s6'→101, 's6'→0, ''→0）
                vm = re.match(r'^(\d{3,})', sheet)
                variant = int(vm.group(1)) if vm else 0
                return (r["skin_num"], variant)
            sorted_files = sorted(quest_files, key=thumb_sort)
            # 最多属性に一致するものを先頭から探す
            if most_common_sn is not None:
                suffix = f"_s{most_common_sn}."
                chosen = next((r["filename"] for r in sorted_files if suffix in r["filename"]),
                              sorted_files[0]["filename"])
            else:
                chosen = sorted_files[0]["filename"]
            g["thumb"] = f"quest/{chosen}"
            break
        cids_key = ",".join(g["char_ids"])
        custom = load_config().get("custom_thumbs", {}).get(cids_key)
        thumb_rel = custom or g["thumb"]
        # サムネイルをbase64に変換してHTMLに埋め込む（HTTPリクエスト削減）
        thumb_data = None
        if thumb_rel:
            thumb_path = os.path.join(IMG_DIR, thumb_rel)
            if os.path.exists(thumb_path):
                try:
                    with open(thumb_path, "rb") as f:
                        raw = f.read()
                    ext = thumb_path.rsplit(".", 1)[-1].lower()
                    mime = "image/webp" if ext == "webp" else f"image/{ext}"
                    thumb_data = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
                except Exception:
                    pass
        chars.append({
            "key": key,
            "name": g["name"],
            "skin_count": g["skin_count"],
            "thumb": thumb_rel,
            "thumb_data": thumb_data,
            "char_ids": cids_key,
        })

    # JS実行時間の最小平均値（サイズ順ソート用）
    je_rows = conn.execute("""
        SELECT char_id, MIN(avg_us) AS min_je FROM (
            SELECT char_id, skin_num, CAST(AVG(exec_time_us) AS INTEGER) AS avg_us
            FROM js_exec WHERE type = 'npc'
            GROUP BY char_id, skin_num
        ) GROUP BY char_id
    """).fetchall()
    je_map = {r["char_id"]: r["min_je"] for r in je_rows}
    for c in chars:
        times = [je_map[cid] for cid in c["char_ids"].split(",") if cid in je_map]
        c["min_size"] = min(times) if times else 999999999

    # character_meta からフィルター用データを取得
    all_ids = [cid for c in chars for cid in c["char_ids"].split(",")]
    if all_ids:
        ph = ",".join("?" * len(all_ids))
        meta_rows = conn.execute(
            f"SELECT char_id, element, weapon, race, series, gender, rarity FROM character_meta WHERE char_id IN ({ph})",
            all_ids
        ).fetchall()
        meta_map = {r["char_id"]: dict(r) for r in meta_rows}
        for c in chars:
            all_meta = [meta_map[cid] for cid in c["char_ids"].split(",") if cid in meta_map]
            if all_meta:
                # 全IDの値を収集（重複除去・順序保持）
                elements = list(dict.fromkeys(m["element"] for m in all_meta if m.get("element")))
                weapons  = list(dict.fromkeys(m["weapon"]  for m in all_meta if m.get("weapon")))
                all_races = []
                for m in all_meta:
                    for r in (m.get("race") or "").split(","):
                        r = r.strip()
                        if r and r not in all_races:
                            all_races.append(r)
                series_list = list(dict.fromkeys(m["series"] for m in all_meta if m.get("series")))
                gender_list = list(dict.fromkeys(m["gender"] for m in all_meta if m.get("gender")))
                rarity_list = list(dict.fromkeys(m["rarity"] for m in all_meta if m.get("rarity")))
                c["element"] = ",".join(elements)
                c["weapon"]  = ",".join(weapons)
                c["race"]    = ",".join(all_races)
                c["series"]  = ",".join(series_list)
                c["gender"]  = ",".join(gender_list)
                c["rarity"]  = ",".join(rarity_list)
            else:
                c["element"] = c["weapon"] = c["race"] = c["series"] = c["gender"] = c["rarity"] = ""
    else:
        for c in chars:
            c["element"] = c["weapon"] = c["race"] = c["series"] = c["gender"] = c["rarity"] = ""

    has_meta = any(c.get("element") for c in chars)

    conn.close()
    return render_template("index.html", chars=chars, has_meta=has_meta)


@app.route("/character/<path:char_ids>")
def detail(char_ids):
    id_list = char_ids.split(",")
    conn = get_db()

    placeholders = ",".join("?" * len(id_list))
    rows = conn.execute(f"""
        SELECT char_id, skin_num, type, sheet, filename,
               CAST(AVG(size_bytes) AS INTEGER) as size_bytes,
               COUNT(*) as samples
        FROM skins
        WHERE char_id IN ({placeholders}) AND type IN ('sd', 'quest', 'raid')
        GROUP BY type, filename
        ORDER BY skin_num, type, sheet
    """, id_list).fetchall()

    names = conn.execute(f"""
        SELECT char_id, skin_num, name FROM characters
        WHERE char_id IN ({placeholders})
    """, id_list).fetchall()
    name_map = {f"{r['char_id']}_{r['skin_num']}": r["name"] for r in names}

    # CJS JSファイルサイズ（npc + phit の合計）をSDスキンごとに取得
    # phit_chars テーブルを結合して汎用phitも対象キャラとして取得
    cjs_rows = conn.execute(f"""
        SELECT char_id, skin_num, type, filename,
               CAST(AVG(size_bytes) AS INTEGER) as size_bytes,
               COUNT(*) as samples
        FROM skins
        WHERE char_id IN ({placeholders}) AND type IN ('cjs_npc', 'cjs_phit', 'phit_png')
        GROUP BY char_id, skin_num, type, filename

        UNION ALL

        SELECT pc.char_id, pc.skin_num, s.type, s.filename,
               CAST(AVG(s.size_bytes) AS INTEGER) as size_bytes,
               COUNT(*) as samples
        FROM skins s
        JOIN phit_chars pc ON s.char_id = pc.phit_id
        WHERE pc.char_id IN ({placeholders}) AND s.type IN ('cjs_phit', 'phit_png')
        GROUP BY pc.char_id, pc.skin_num, s.type, s.filename

        ORDER BY char_id, skin_num, type, filename
    """, id_list + id_list).fetchall()

    # phit JS: char_id -> {skin_num -> size}（cjs_mapに加算用）
    # phit PNG: char_id -> {skin_num -> size}（SD PNG totalに加算用）
    phit_js_map  = {}
    phit_png_map = {}
    for r in cjs_rows:
        if r["type"] == "cjs_phit":
            d = phit_js_map
        elif r["type"] == "phit_png":
            d = phit_png_map
        else:
            continue
        cid = r["char_id"]
        if cid not in d:
            d[cid] = {}
        d[cid][r["skin_num"]] = r["size_bytes"]

    conn.close()

    def _phit_lookup(mapping, char_id, skin_num):
        """スキン専用→デフォルト("")→任意のスキンの順で検索。
        汎用phit(bw_0012等)は同一ファイルを別skin_numで収集することがあるため最後に任意fallback"""
        skins = mapping.get(char_id, {})
        if not skins:
            return 0
        return skins.get(skin_num) or skins.get("") or next(iter(skins.values()), 0)

    # (char_id_skin_num) -> {total, files}  ※JSサイズのみ
    cjs_map = {}
    for r in cjs_rows:
        if r["type"] != "cjs_npc":
            continue
        k = f"{r['char_id']}_{r['skin_num']}"
        if k not in cjs_map:
            cjs_map[k] = {"total": 0, "files": []}
        cjs_map[k]["total"] += r["size_bytes"]
        cjs_map[k]["files"].append({
            "filename": r["filename"],
            "size_bytes": r["size_bytes"],
            "samples": r["samples"],
        })

    # phit JS サイズを cjs_map に加算
    for k, v in cjs_map.items():
        char_id, skin_num = k.rsplit("_", 1)
        phit_js = _phit_lookup(phit_js_map, char_id, skin_num)
        if phit_js:
            v["total"] += phit_js
            v["phit_js_size"] = phit_js

    def strip_sn(sheet):
        """sheetから属性を示す _sN 部分を除去して正規化キーを返す"""
        if not sheet:
            return ""
        m = re.match(r'^(\d{3,})_s\d+$', sheet)
        if m:
            return m.group(1)
        if re.match(r'^s\d+$', sheet):
            return ""
        return sheet

    def get_sn(sheet):
        """sheetから属性番号(sN)を取り出す。なければNone"""
        if not sheet:
            return None
        m = re.search(r'_s(\d+)$', sheet)
        if m:
            return int(m.group(1))
        m = re.match(r'^s(\d+)$', sheet)
        if m:
            return int(m.group(1))
        return None

    # --- 第1パス: 全行を一時バッファに積みながら sN 出現頻度を集計 ---
    from collections import Counter
    sn_freq = Counter()  # sN値(int) → ページ内の出現回数
    raw = {"sd": {}, "quest": {}, "raid": {}}  # key → {"meta":..., "variants":[...]}

    for row in rows:
        t = row["type"]
        base_key = f"{row['char_id']}_{row['skin_num']}"
        if t in ("quest", "raid"):
            norm_sheet = strip_sn(row["sheet"] or "")
            key = f"{base_key}_{norm_sheet}" if norm_sheet else base_key
            sn = get_sn(row["sheet"] or "")
            if sn is not None:
                sn_freq[sn] += 1
        else:
            key = base_key
            norm_sheet = row["sheet"] or ""
            sn = None

        if key not in raw[t]:
            raw[t][key] = {
                "skin_num": row["skin_num"],
                "char_id": row["char_id"],
                "norm_sheet": norm_sheet,
                "variants": [],
            }
        raw[t][key]["variants"].append({
            "filename": row["filename"],
            "size_bytes": row["size_bytes"],
            "sheet_orig": row["sheet"] or "",
            "samples": row["samples"],
            "sn": sn,
        })

    # ページ内で最も多く使われている属性(sN)
    most_common_sn = sn_freq.most_common(1)[0][0] if sn_freq else None

    # --- 第2パス: by_type を構築。quest/raidはsNバリアントをマージ ---
    by_type = {"sd": {}, "quest": {}, "raid": {}}
    for t in ("sd", "quest", "raid"):
        for key, data in raw[t].items():
            variants = data["variants"]
            norm_sheet = data["norm_sheet"]
            if t in ("quest", "raid") and len(variants) > 1:
                # 最多属性に一致するファイルを表示用に選ぶ（なければ先頭）
                best = next(
                    (v for v in variants if v["sn"] == most_common_sn),
                    variants[0]
                )
                avg_size = sum(v["size_bytes"] for v in variants) // len(variants)
                by_type[t][key] = {
                    "files": [{"filename": best["filename"],
                               "size_bytes": avg_size,
                               "sheet": norm_sheet,
                               "samples": best["samples"]}],
                    "total": avg_size,
                    "skin_num": data["skin_num"],
                    "char_id": data["char_id"],
                    "sheet": norm_sheet,
                }
            else:
                by_type[t][key] = {
                    "files": [{"filename": v["filename"],
                               "size_bytes": v["size_bytes"],
                               "sheet": norm_sheet if t in ("quest", "raid") else v["sheet_orig"],
                               "samples": v["samples"]} for v in variants],
                    "total": sum(v["size_bytes"] for v in variants),
                    "skin_num": data["skin_num"],
                    "char_id": data["char_id"],
                    "sheet": norm_sheet,
                }

    def sheet_sort_key(sheet):
        """sheet値をソートキーに変換
        '' / 's6' (差分なし) → (0, 0, sN)
        '101' / '101_s6' (差分101) → (1, 101, sN)
        '102' / '102_s6' (差分102) → (1, 102, sN)
        """
        if not sheet:
            return (0, 0, 0)
        # 差分番号あり: '101', '101_s6'
        m = re.match(r'^(\d{3,})(?:_s(\d+))?$', sheet)
        if m:
            variant = int(m.group(1))
            sn = int(m.group(2)) if m.group(2) else 0
            return (1, variant, sn)
        # sNのみ (差分なし): 's6'
        m2 = re.match(r'^s(\d+)$', sheet)
        if m2:
            return (0, 0, int(m2.group(1)))
        return (2, 0, 0)

    # char_id → skin_num → sheet(差分なし→101→102...) の順でソート
    for t in by_type:
        by_type[t] = dict(sorted(
            by_type[t].items(),
            key=lambda x: (x[1]["char_id"], x[1]["skin_num"], sheet_sort_key(x[1]["sheet"]))
        ))

    # phit PNG サイズを SD PNG total に加算
    for k, v in by_type["sd"].items():
        phit_png = _phit_lookup(phit_png_map, v["char_id"], v["skin_num"])
        if phit_png:
            v["total"] += phit_png
            v["phit_png_size"] = phit_png

    def lightest(d):
        return min(d, key=lambda k: d[k]["total"]) if d else None

    # ── JS実行時間データを先に取得（lightest_sdで使用）────────────────
    conn2 = get_db()
    je_avg_rows = conn2.execute(f"""
        SELECT char_id, skin_num, type,
               CAST(AVG(exec_time_us) AS INTEGER) AS avg_us,
               MIN(exec_time_us) AS min_us,
               MAX(exec_time_us) AS max_us,
               COUNT(*) AS cnt
        FROM js_exec
        WHERE char_id IN ({placeholders})
        GROUP BY char_id, skin_num, type
    """, id_list).fetchall()

    je_raw_rows = conn2.execute(f"""
        SELECT id, char_id, skin_num, type, exec_time_us
        FROM js_exec
        WHERE char_id IN ({placeholders})
        ORDER BY char_id, skin_num, type, id
    """, id_list).fetchall()
    conn2.close()

    js_exec_map = {}
    for r in je_avg_rows:
        k = f"{r['char_id']}_{r['skin_num']}"
        if k not in js_exec_map:
            js_exec_map[k] = {}
        js_exec_map[k][r["type"]] = {
            "avg": r["avg_us"], "min": r["min_us"],
            "max": r["max_us"], "cnt": r["cnt"],
        }

    js_exec_raw = {}
    for r in je_raw_rows:
        k = f"{r['char_id']}_{r['skin_num']}"
        if k not in js_exec_raw:
            js_exec_raw[k] = []
        js_exec_raw[k].append({
            "id": r["id"], "type": r["type"], "exec_time_us": r["exec_time_us"],
        })

    # SD最軽量判定:
    #   JS実行時間あり → 最小から10%以内は同一グループとしてPNG比較
    #   JS実行時間なし → CJSサイズ→PNGフォールバック
    JS_MARGIN = 0.10  # 10%以内は誤差とみなしてPNGで比較
    def lightest_sd(d):
        if not d:
            return None
        def get_je_avg(k):
            je = js_exec_map.get(k, {}).get("npc")
            return je["avg"] if je else None

        je_vals = {k: get_je_avg(k) for k in d}
        valid_je = [v for v in je_vals.values() if v is not None]

        if valid_je:
            min_je = min(valid_je)
            threshold = min_je * (1 + JS_MARGIN)
            def sd_key(k):
                skin = d[k]
                je = je_vals[k]
                if je is None:
                    return (2, 999999999, skin["total"])
                if je <= threshold:
                    return (0, 0, skin["total"])   # 誤差内：PNGで比較
                return (1, je, skin["total"])       # 誤差外：JS実行時間で比較
        else:
            # JSデータなし → CJS→PNG
            def sd_key(k):
                skin = d[k]
                cjs_total = cjs_map.get(f"{skin['char_id']}_{skin['skin_num']}", {}).get("total", 999999999)
                return (cjs_total, skin["total"])

        return min(d, key=sd_key)

    min_sd    = lightest_sd(by_type["sd"])
    min_quest = lightest(by_type["quest"])
    min_raid  = lightest(by_type["raid"])

    # 推奨モード: SD最軽量の char_id + skin_num に合わせた編成・バトルのキー
    if min_sd:
        rec_char_id  = by_type["sd"][min_sd]["char_id"]
        rec_skin_num = by_type["sd"][min_sd]["skin_num"]
    else:
        rec_char_id = rec_skin_num = None
    # 推奨グループ内（同じchar_id）でそれぞれ最軽量を選出
    rec_quest = min(
        (k for k, v in by_type["quest"].items() if v["char_id"] == rec_char_id),
        key=lambda k: by_type["quest"][k]["total"],
        default=min_quest,
    )
    rec_raid = min(
        (k for k, v in by_type["raid"].items() if v["char_id"] == rec_char_id),
        key=lambda k: by_type["raid"][k]["total"],
        default=min_raid,
    )

    # サブモード: SD PNG最軽量グループ → その中で編成最軽量
    min_sd_png = lightest(by_type["sd"])  # PNG基準の最軽量SDキー
    sub_char_id = by_type["sd"][min_sd_png]["char_id"] if min_sd_png else rec_char_id
    sub_quest = min(
        (k for k, v in by_type["quest"].items() if v["char_id"] == sub_char_id),
        key=lambda k: by_type["quest"][k]["total"],
        default=min_quest,
    )

    char_name = next(iter(name_map.values()), None) or f"ID: {char_ids}"

    # キャラメタ（属性・得意武器・シリーズ）
    conn2 = get_db()
    meta_rows = conn2.execute(
        f"SELECT char_id, element, weapon, series, rarity FROM character_meta WHERE char_id IN ({placeholders})",
        id_list
    ).fetchall()
    conn2.close()

    # char_id → メタ辞書（スキンラベル用）
    skin_meta_map = {r["char_id"]: dict(r) for r in meta_rows}

    def _collect_meta(field):
        seen, result = set(), []
        for r in meta_rows:
            for v in (r[field] or "").split(","):
                v = v.strip()
                if v and v not in seen:
                    seen.add(v)
                    result.append(v)
        return ",".join(result)

    char_element = _collect_meta("element")
    char_weapon  = _collect_meta("weapon")
    char_series  = _collect_meta("series")
    char_rarity  = _collect_meta("rarity")

    # 全画像をbase64化してHTTPリクエストを削減
    for t, skins in by_type.items():
        for skin in skins.values():
            for f in skin["files"]:
                f["img_data"] = img_data_uri(t, f["filename"])

    return render_template("detail.html",
        char_id=char_ids,
        char_name=char_name,
        char_element=char_element,
        char_weapon=char_weapon,
        char_series=char_series,
        char_rarity=char_rarity,
        skin_meta_map=skin_meta_map,
        by_type=by_type,
        name_map=name_map,
        min_sd=min_sd,
        min_quest=min_quest,
        min_raid=min_raid,
        rec_quest=rec_quest,
        rec_raid=rec_raid,
        rec_char_id=rec_char_id,
        sub_char_id=sub_char_id,
        sub_quest=sub_quest,
        cjs_map=cjs_map,
        phit_js_map=phit_js_map,
        phit_png_map=phit_png_map,
        js_exec_map=js_exec_map,
        js_exec_raw=js_exec_raw,
        show_cjs=load_config().get("show_cjs", False),
        section_order=load_config()["section_order"],
    )


@app.route("/character/<path:char_ids>/thumb", methods=["GET", "POST"])
def thumb_settings(char_ids):
    if request.method == "POST":
        chosen = request.form.get("thumb", "")
        cfg = load_config()
        thumbs = cfg.setdefault("custom_thumbs", {})
        if chosen:
            thumbs[char_ids] = chosen
        else:
            thumbs.pop(char_ids, None)
        save_config(cfg)
        return redirect(url_for("detail", char_ids=char_ids))

    id_list = char_ids.split(",")
    conn = get_db()
    placeholders = ",".join("?" * len(id_list))
    name_row = conn.execute(
        f"SELECT name FROM characters WHERE char_id IN ({placeholders}) LIMIT 1", id_list
    ).fetchone()
    char_name = name_row["name"] if name_row else char_ids

    rows = conn.execute(f"""
        SELECT DISTINCT filename FROM skins
        WHERE char_id IN ({placeholders}) AND type = 'quest'
        ORDER BY filename
    """, id_list).fetchall()
    images = []
    for r in rows:
        path = f"quest/{r['filename']}"
        images.append({
            "type": "quest",
            "path": path,
            "img_data": img_data_uri("quest", r["filename"]),
        })
    conn.close()

    current = load_config().get("custom_thumbs", {}).get(char_ids, "")
    return render_template("thumb.html",
        char_ids=char_ids, char_name=char_name,
        images=images, current=current)


@app.route("/character/<path:char_ids>/reset_js", methods=["POST"])
def reset_js(char_ids):
    # skins[] は "char_id_skin_num" 形式のリスト
    skins = request.form.getlist("skins[]")
    conn = get_db()
    for s in skins:
        parts = s.rsplit("_", 1)
        if len(parts) == 2:
            conn.execute(
                "DELETE FROM skins WHERE char_id=? AND skin_num=? AND type='cjs_npc'",
                (parts[0], parts[1])
            )
    conn.commit()
    conn.close()
    return redirect(url_for("detail", char_ids=char_ids))


def _parse_image_filename(fname, t):
    """ファイル名からメタデータを解析。失敗時はNone"""
    pat_sd = re.compile(r'^(?:npc_)?(\d+)_(\d+)(?:_s\d+)?(?:_([a-z]))?\.png$')
    pat_qr = re.compile(r'^(\d+)_(\d+)(?:_(\d{3,}))?(?:_s(\d+))?\.jpg$')
    if t == "sd":
        m = pat_sd.match(fname)
        if not m:
            return None
        char_id, skin_num, letter = m.group(1), m.group(2), m.group(3) or ""
        return {"char_id": char_id, "skin_num": skin_num, "sheet": letter}
    else:
        m = pat_qr.match(fname)
        if not m:
            return None
        char_id, skin_num = m.group(1), m.group(2)
        variant, sn = m.group(3), m.group(4)
        if variant and sn:
            sheet = f"{variant}_s{sn}"
        elif variant:
            sheet = variant
        elif sn:
            sheet = f"s{sn}"
        else:
            sheet = ""
        return {"char_id": char_id, "skin_num": skin_num, "sheet": sheet}


@app.route("/check")
def check():
    """画像パスを指定して表示されない原因を診断"""
    path_input = request.args.get("path", "").strip().strip('"').strip("'")
    result = None

    if path_input:
        # パス正規化: images/raid/foo.jpg → type=raid, fname=foo.jpg
        # または単純なファイル名: foo.jpg → typeをdirから推定
        norm = path_input.replace("\\", "/").lstrip("/")
        parts = norm.split("/")
        fname = parts[-1]
        # typeをパスから取得、なければディスクを実際に探す
        if len(parts) >= 2 and parts[-2] in ("sd", "quest", "raid"):
            t = parts[-2]
        elif fname.endswith(".png"):
            t = "sd"
        else:
            # sd/quest/raid を順に探して見つかったものを採用
            t = next((d for d in ("sd", "quest", "raid")
                      if os.path.exists(os.path.join(IMG_DIR, d, fname))), None)

        result = {"path": path_input, "fname": fname, "type": t, "issues": [], "db_rows": [], "char_name": None, "on_disk": False}

        fpath = os.path.join(IMG_DIR, t, fname) if t else None
        result["on_disk"] = bool(fpath and os.path.exists(fpath))
        if not result["on_disk"]:
            searched = ", ".join(os.path.join(IMG_DIR, d, fname) for d in ("sd", "quest", "raid"))
            result["issues"].append(f"ファイルがディスク上に存在しない (探索先: {searched})")

        conn = get_db()
        # DBレコードを確認
        db_rows = conn.execute(
            "SELECT * FROM skins WHERE filename = ?", (fname,)
        ).fetchall()
        result["db_rows"] = [dict(r) for r in db_rows]

        if not db_rows:
            result["issues"].append("skins テーブルに未登録")
            # ファイル名解析してインポート候補を作る
            if t:
                parsed = _parse_image_filename(fname, t)
                result["import_candidate"] = {**parsed, "type": t, "size": os.path.getsize(fpath) if result["on_disk"] else 0} if parsed else None
        else:
            char_id = db_rows[0]["char_id"]
            name_row = conn.execute(
                "SELECT name FROM characters WHERE char_id = ?", (char_id,)
            ).fetchone()
            if name_row:
                result["char_name"] = name_row["name"]
                if not name_row["name"]:
                    result["issues"].append("characters テーブルに登録されているが name が空 → 一覧に表示されない")
            else:
                result["issues"].append("characters テーブルに未登録 → 一覧に表示されない（名前が取得されていない）")

        conn.close()
        if not result["issues"]:
            result["issues"].append("問題なし: DB 登録済み・キャラ名あり")

    return render_template("check.html", result=result)


@app.route("/check/import", methods=["POST"])
def check_import():
    """指定ファイルをDBに登録（ディスクに既にある場合）"""
    fname = request.form.get("filename", "")
    t     = request.form.get("type", "")
    if not fname or not t:
        return redirect(url_for("check"))

    parsed = _parse_image_filename(fname, t)
    if not parsed:
        return redirect(url_for("check"))

    fpath = os.path.join(IMG_DIR, t, fname)
    size = os.path.getsize(fpath) if os.path.exists(fpath) else 0

    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO skins (char_id, skin_num, type, sheet, filename, size_bytes) VALUES (?,?,?,?,?,?)",
        (parsed["char_id"], parsed["skin_num"], t, parsed["sheet"], fname, size)
    )
    conn.commit()
    conn.close()
    flash("DB に登録しました")
    return redirect(url_for("check", path=request.form.get("path", "")))


@app.route("/check/upload", methods=["POST"])
def check_upload():
    """ファイルをアップロードしてディスク保存＋DB登録"""
    path_input = request.form.get("path", "")
    t          = request.form.get("type", "")
    fname      = request.form.get("filename", "")
    file       = request.files.get("file")

    if not file or not t or not fname:
        flash("ファイル・タイプ・ファイル名が必要です")
        return redirect(url_for("check", path=path_input))

    parsed = _parse_image_filename(fname, t)
    if not parsed:
        flash(f"ファイル名の解析に失敗: {fname}")
        return redirect(url_for("check", path=path_input))

    # ディスクに保存
    dest_dir = os.path.join(IMG_DIR, t)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, fname)
    file.save(dest_path)
    size = os.path.getsize(dest_path)

    # DB登録
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO skins (char_id, skin_num, type, sheet, filename, size_bytes) VALUES (?,?,?,?,?,?)",
        (parsed["char_id"], parsed["skin_num"], t, parsed["sheet"], fname, size)
    )
    conn.commit()
    conn.close()
    flash(f"保存・登録完了: {fname} ({size:,} B)")
    return redirect(url_for("check", path=path_input))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if request.method == "POST":
        order = request.form.getlist("section_order")
        valid = {"sd", "quest", "raid"}
        if set(order) == valid and len(order) == 3:
            cfg["section_order"] = order
        else:
            flash("無効な順序です", "warning")
            return redirect(url_for("settings"))
        cfg["show_cjs"] = request.form.get("show_cjs") == "1"
        save_config(cfg)
        flash("設定を保存しました", "success")
        return redirect(url_for("settings"))
    return render_template("settings.html", cfg=cfg)


_fetch_state = {"running": False, "saved": 0, "total": 0, "error": None, "done": False}

def _run_fetch_async():
    import fetch_meta as fm
    _fetch_state.update(running=True, error=None, done=False)
    try:
        saved, total = fm.run_fetch(db_path=DB_PATH)
        _fetch_state.update(saved=saved, total=total)
    except Exception as e:
        _fetch_state["error"] = str(e)
    finally:
        _fetch_state.update(running=False, done=True)

@app.route("/api/fetch_meta", methods=["POST"])
def api_fetch_meta():
    if _fetch_state["running"]:
        return {"ok": False, "error": "already running"}, 409
    threading.Thread(target=_run_fetch_async, daemon=True).start()
    return {"ok": True}

@app.route("/api/fetch_meta/status")
def api_fetch_meta_status():
    return dict(_fetch_state)


@app.route("/api/js_exec/prune", methods=["POST"])
def prune_js_exec():
    """IQRで外れ値を検出して削除"""
    data = request.get_json(silent=True) or {}
    char_id  = data.get("char_id", "")
    skin_num = data.get("skin_num", "")
    js_type  = data.get("type", "npc")
    if not char_id:
        return {"ok": False, "error": "invalid"}, 400

    conn = get_db()
    rows = conn.execute(
        "SELECT id, exec_time_us FROM js_exec WHERE char_id=? AND skin_num=? AND type=? ORDER BY exec_time_us",
        (char_id, skin_num, js_type)
    ).fetchall()

    if len(rows) < 4:
        conn.close()
        return {"ok": True, "deleted": 0, "deleted_ids": []}

    vals = [r["exec_time_us"] for r in rows]
    n = len(vals)
    q1 = vals[n // 4]
    q3 = vals[3 * n // 4]
    upper = q3 + 1.5 * (q3 - q1)

    to_delete = [r["id"] for r in rows if r["exec_time_us"] > upper]
    for rid in to_delete:
        conn.execute("DELETE FROM js_exec WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return {"ok": True, "deleted": len(to_delete), "deleted_ids": to_delete}


@app.route("/api/js_exec/<int:record_id>", methods=["DELETE"])
def delete_js_exec(record_id):
    """個別レコードを削除"""
    conn = get_db()
    conn.execute("DELETE FROM js_exec WHERE id=?", (record_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


def _download_and_save_image(url, rtype, filename, char_id, skin_num, sheet):
    """CDN URLから画像を取得してWebPに変換・保存（バックグラウンドスレッド用）"""
    if not _HAS_REQUESTS or not _HAS_PIL:
        return
    webp_name = re.sub(r'\.(png|jpg|jpeg)$', '.webp', filename, flags=re.IGNORECASE)
    save_dir  = os.path.join(IMG_DIR, rtype)
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, webp_name)
    if os.path.exists(path):
        return
    try:
        resp = _requests.get(url, timeout=15)
        resp.raise_for_status()
        img = _Image.open(io.BytesIO(resp.content))
        img.save(path, "WEBP", quality=85, method=6)
    except Exception as e:
        print(f"[collect] 画像ダウンロード失敗 {filename}: {e}")


@app.route("/api/collect_skin", methods=["POST"])
def api_collect_skin():
    """拡張機能からスキンファイルサイズを受信してDBに保存"""
    data       = request.get_json(silent=True) or {}
    char_id    = data.get("char_id", "")
    skin_num   = data.get("skin_num", "")
    rtype      = data.get("type", "")
    sheet      = data.get("sheet", "")
    filename   = data.get("filename", "")
    size_bytes = int(data.get("size_bytes", 0))
    url        = data.get("url", "")

    if not all([char_id, rtype, filename]) or size_bytes <= 0:
        return {"ok": False, "error": "missing fields"}, 400

    # 画像ファイルは .webp 名でDBに保存
    is_image = rtype in ("sd", "quest", "raid", "phit_png")
    if is_image:
        save_name = re.sub(r'\.(png|jpg|jpeg)$', '.webp', filename, flags=re.IGNORECASE)
    else:
        save_name = filename

    conn = get_db()
    exists = conn.execute(
        "SELECT 1 FROM skins WHERE filename = ? AND type = ? LIMIT 1",
        (save_name, rtype)
    ).fetchone()
    if not exists:
        conn.execute("""
            INSERT INTO skins (char_id, skin_num, type, sheet, filename, size_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (char_id, skin_num, rtype, sheet, save_name, size_bytes))
        conn.commit()

        # 画像はバックグラウンドでCDNからダウンロード
        if is_image and url:
            threading.Thread(
                target=_download_and_save_image,
                args=(url, rtype, filename, char_id, skin_num, sheet),
                daemon=True
            ).start()
    conn.close()
    return {"ok": True}


@app.route("/api/collect_char", methods=["POST"])
def api_collect_char():
    """拡張機能からキャラ名を受信してDBに保存"""
    data  = request.get_json(silent=True) or {}
    chars = data.get("chars", [])
    if not chars:
        return {"ok": False, "error": "no chars"}, 400

    conn = get_db()
    for c in chars:
        char_id  = c.get("char_id", "")
        skin_num = c.get("skin_num", "")
        name     = c.get("name", "")
        if not all([char_id, skin_num, name]):
            continue
        conn.execute("""
            INSERT INTO characters (char_id, skin_num, name)
            VALUES (?, ?, ?)
            ON CONFLICT(char_id, skin_num) DO UPDATE SET name = excluded.name
        """, (char_id, skin_num, name))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.route("/api/phit_char", methods=["POST"])
def api_phit_char():
    """NPC JSから抽出したphit→キャラ紐付けを保存"""
    data     = request.get_json(silent=True) or {}
    char_id  = data.get("char_id", "")
    skin_num = data.get("skin_num", "")
    phit_id  = data.get("phit_id", "")
    if not all([char_id, phit_id]):
        return {"ok": False, "error": "missing fields"}, 400
    conn = get_db()
    conn.execute("""
        INSERT OR IGNORE INTO phit_chars (char_id, skin_num, phit_id)
        VALUES (?, ?, ?)
    """, (char_id, skin_num, phit_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.route("/api/js_exec", methods=["POST"])
def api_js_exec():
    """拡張機能からJS実行時間を受信してDBに保存"""
    data = request.get_json(silent=True) or {}
    char_id      = data.get("char_id", "")
    skin_num     = data.get("skin_num", "")
    exec_time_us = int(data.get("exec_time_us", 0))
    cjs_type     = data.get("type", "npc")
    url          = data.get("url", "")

    if not char_id or exec_time_us <= 0:
        return {"ok": False, "error": "invalid"}, 400

    conn = get_db()
    conn.execute(
        "INSERT INTO js_exec (char_id, skin_num, type, exec_time_us, trace_file) VALUES (?,?,?,?,?)",
        (char_id, skin_num, cjs_type, exec_time_us, url)
    )
    conn.commit()
    conn.close()
    return {"ok": True}


migrate_db()

if __name__ == "__main__":
    app.run(debug=True, threaded=True)
