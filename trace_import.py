"""
Chrome DevTools パフォーマンストレースから CJS の JS 実行時間を抽出し DB に取込む。

使い方:
    python trace_import.py               # traces/ フォルダ内の全JSONを処理・削除
    python trace_import.py <file> ...    # 指定ファイルを処理・削除

JS実行時間の定義:
    XHRLoad(npc_XXXX.js) の終了時刻 → ResourceSendRequest(npc_XXXX.png) の開始時刻
    = メインスレッドがブロックされていた CPU 時間
"""

import json
import os
import re
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skinsize.db")
MAX_SAMPLES = 10

# /js/cjs/npc_3040115000_01.js
CJS_JS_PAT  = re.compile(r'/cjs/npc_(\d+)_(\d+)\.js')
# /img_low/sp/cjs/npc_3040115000_01.png  or  npc_3040115000_01_a.png
CJS_PNG_PAT = re.compile(r'/cjs/npc_(\d+)_(\d+)(?:_[a-z])?\.png')


def init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS js_exec (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            char_id     TEXT    NOT NULL,
            skin_num    TEXT    NOT NULL,
            exec_time_us INTEGER NOT NULL,
            trace_file  TEXT
        )
    """)
    conn.commit()


def parse_trace(trace_path):
    """トレースJSONを解析して (char_id, skin_num, exec_time_us) のリストを返す"""
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = data.get("traceEvents", data) if isinstance(data, dict) else data
    events = [e for e in events if "ts" in e]
    events.sort(key=lambda e: e["ts"])

    # XHRLoad 終了時刻: (char_id, skin_num) -> [end_ts, ...]
    xhr_ends = {}
    # ResourceSendRequest (PNG) 開始時刻: (char_id, skin_num) -> [ts, ...]
    png_starts = {}

    for ev in events:
        name = ev.get("name", "")
        url  = ev.get("args", {}).get("data", {}).get("url", "")
        if not url:
            continue

        if name == "XHRLoad":
            m = CJS_JS_PAT.search(url)
            if m:
                key = (m.group(1), m.group(2))
                end_ts = ev["ts"] + ev.get("dur", 0)
                xhr_ends.setdefault(key, []).append(end_ts)

        elif name == "ResourceSendRequest":
            m = CJS_PNG_PAT.search(url)
            if m:
                key = (m.group(1), m.group(2))
                png_starts.setdefault(key, []).append(ev["ts"])

    results = []
    for key, end_times in xhr_ends.items():
        char_id, skin_num = key
        pngs = sorted(png_starts.get(key, []))
        # 最初の XHRLoad に対応する PNG だけを使う（同一トレース内の2回目以降は無視）
        end_ts = min(end_times)
        next_png = next((t for t in pngs if t > end_ts), None)
        if next_png is not None:
            exec_us = next_png - end_ts
            if exec_us >= 0:
                results.append((char_id, skin_num, exec_us))

    return results


def import_file(conn, trace_path):
    trace_name = os.path.basename(trace_path)
    results = parse_trace(trace_path)

    if not results:
        print(f"[trace] {trace_name}: 該当データなし")
        os.remove(trace_path)
        return

    inserted = skipped = 0
    for char_id, skin_num, exec_us in results:
        count = conn.execute(
            "SELECT COUNT(*) FROM js_exec WHERE char_id=? AND skin_num=?",
            (char_id, skin_num)
        ).fetchone()[0]
        if count < MAX_SAMPLES:
            conn.execute(
                "INSERT INTO js_exec (char_id, skin_num, exec_time_us, trace_file) VALUES (?,?,?,?)",
                (char_id, skin_num, exec_us, trace_name)
            )
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    os.remove(trace_path)
    print(f"[trace] {trace_name}: {inserted} 件インポート"
          + (f", {skipped} 件スキップ（上限{MAX_SAMPLES}件）" if skipped else "")
          + " → 削除済み")


TRACES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "traces")

if __name__ == "__main__":
    if len(sys.argv) >= 2:
        targets = sys.argv[1:]
    else:
        targets = sorted(
            os.path.join(TRACES_DIR, f)
            for f in os.listdir(TRACES_DIR)
            if f.endswith(".json")
        )
        if not targets:
            print("[trace] traces/ フォルダに JSON ファイルがありません")
            sys.exit(0)

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    init_table(conn)

    for path in targets:
        if not os.path.exists(path):
            print(f"[trace] ファイルが見つかりません: {path}")
            continue
        import_file(conn, path)

    conn.close()
