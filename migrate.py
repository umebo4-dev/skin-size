"""
skinsテーブルのUNIQUE制約を除去するマイグレーション。
既存データはそのまま保持。
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skinsize.db")
conn = sqlite3.connect(DB_PATH)

conn.executescript("""
    BEGIN;

    -- 既存データを一時テーブルに退避
    CREATE TEMPORARY TABLE skins_backup AS SELECT * FROM skins;

    -- 旧テーブル削除（UNIQUE制約ごと）
    DROP TABLE skins;

    -- UNIQUE制約なしで再作成
    CREATE TABLE skins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        char_id TEXT NOT NULL,
        skin_num TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL,
        sheet TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL,
        size_bytes INTEGER NOT NULL
    );

    -- データを復元（重複は1件のみ残す）
    INSERT INTO skins (char_id, skin_num, type, sheet, filename, size_bytes)
    SELECT char_id,
           COALESCE(skin_num, ''),
           type,
           COALESCE(sheet, ''),
           filename,
           size_bytes
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY filename ORDER BY id) AS rn
        FROM skins_backup
    )
    WHERE rn = 1;

    COMMIT;
""")

conn.close()
print("マイグレーション完了")
