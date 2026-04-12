import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skinsize.db")

conn = sqlite3.connect(DB_PATH)
rows = conn.execute("SELECT * FROM skins ORDER BY char_id, skin_num, type, sheet").fetchall()
conn.close()

if not rows:
    print("データなし")
else:
    print(f"{'ID':<12} {'スキン':<6} {'種類':<6} {'シート':<6} {'ファイル名':<40} {'サイズ':>10}")
    print("-" * 85)
    for row in rows:
        _, char_id, skin_num, rtype, sheet, filename, size = row
        print(f"{char_id:<12} {skin_num:<6} {rtype:<6} {str(sheet):<6} {filename:<40} {size:>10,}")
    print(f"\n合計 {len(rows)} 件")
