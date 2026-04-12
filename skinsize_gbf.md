# skinsize_gbf

GBFのスキン（キャラ画像等）のサイズをmitmproxyで収集しDBに蓄積するプロジェクト。

## 目的
スキンごとの総データサイズを比較し、最も軽いスキンを把握する。

## 方針
- 既存の `C:\mmpy\dn.py` とは別スクリプトとして作成
- mitmproxyのレスポンスを受け取り、対象データをDBに保存するだけのシンプルな構成

## 収集対象

ベースURL: `https://prd-game-a-granbluefantasy.akamaized.net/assets/img_low/sp/`

| 種類 | パス | パターン | 形式 |
|------|------|----------|------|
| SDスプライト | `cjs/` | `npc_{ID}_{スキン}.png` or `npc_{ID}_{スキン}_{シート}.png` | PNG |
| 編成画面 | `assets/npc/quest/skin/` | `{ID}_{スキン}[_{差分番号}]_s{N}.jpg` | JPG |
| バトル中 | `assets/npc/raid_normal/` | `{ID}_{スキン}.jpg` | JPG |

- SDスプライトはシート1枚ならサフィックスなし、複数枚なら a/b/c...
- `img_low` は固定

### 画像枚数の仕様
| 種類 | 枚数 | 備考 |
|------|------|------|
| SD | 1枚以上 | シート(a/b/c...)で複数になる |
| 編成画面 | 原則1枚 | 差分(101, 102等)がある場合は複数。差分数はキャラ依存 |
| バトル中 | 必ず1枚 | シートなし・差分なし |

## 決定事項
- DB: SQLite（`skinsize.db`）
- スクリプト保存場所: `C:\Users\那智勝浦\Claude-Code\output\skinsize_gbf\`

---

## 計測指標の調査結果（2026-03-27）

### プロファイル（Chrome DevTools トレース）から何が取れるか

Chrome DevToolsのパフォーマンスタブで保存したトレースJSONには以下のイベントが含まれる。

| イベント名 | 取れるデータ | 結合キー |
|-----------|------------|---------|
| `ResourceSendRequest` | URL, resourceType, priority | `requestId` |
| `ResourceReceiveResponse` | mimeType, fromCache, ネットワークtiming詳細（DNS/TCP/TTFB等） | `requestId` |
| `ResourceFinish` | `decodedBodyLength`（展開後サイズ）, `encodedDataLength`（転送サイズ） | `requestId` |
| `Decode Image` | `dur`（デコード時間μs）, imageType | URLとの直接紐付けなし |

3イベントを `requestId` で結合すると **URL × サイズ × タイミング** が揃う。

---

### キャラスキンのロードシーケンス

1つのキャラスキン（例: `3710191000_01`）は以下の順でロードされる。

```
ResourceSendRequest  → /js/model/manifest/npc_XXXXXXXX_XX.js   （マニフェスト）
EvaluateScript       → マニフェスト実行（数十μs）
                        ↓ マニフェストが cjs/ を XHR でリクエスト
ResourceSendRequest  → /js/cjs/npc_XXXXXXXX_XX.js              （アニメデータ本体）
XHRLoad              → cjs JS 受信完了
                        ↓ ★ JS実行（数十ms）← ここがボトルネック
ResourceSendRequest  → /img_low/sp/cjs/npc_XXXXXXXX_XX.png     （スプライトシート）
```

---

### 指標の種類と特性

#### 1. JS実行時間（最重要指標）

- **定義**: `XHRLoad.ts + XHRLoad.dur` → 次の `ResourceSendRequest(PNG).ts` の差
- **意味**: `cjs/npc_XXXXXXXX.js` の初期化にかかったCPU時間。アニメーションの全フレーム・オブジェクトを生成する処理。
- **体感との対応**: この時間だけメインスレッドがブロックされる → フリーズ感に直結
- **パフォーマンスタブ上の表示**: `a.npc_XXXXXXXX_XX: XX.XXms` として炎グラフに現れる

#### 2. ファイルサイズ（安定した代替指標）

- `decodedBodyLength`（`ResourceFinish` から取得）
- キャッシュヒット時は `encodedDataLength = 0` になるが `decodedBodyLength` は常に取れる
- JS実行時間と高い相関があり、ブレがないため比較用途に向く

---

### ブレ・精度について

**JS実行時間はブレる**。原因：

| 要因 | 影響度 |
|------|--------|
| メインスレッドの混み具合（他JS・アニメーションフレームとの競合） | 大（最大の原因） |
| GCのタイミング | 中 |
| V8コードキャッシュの有無（初回 vs 2回目以降） | 中 |
| CPU熱・他プロセス負荷 | 小〜中 |

→ 複数回計測して**中央値**を使うか、**ファイルサイズを代替指標**にすることを推奨。

**ネットワーク環境はJS実行時間に影響しない**（ファイル受信後の処理のため）。
ファイルサイズ・転送量にはもちろん影響する。

---

### mitmproxyとの役割分担

| 指標 | mitmproxyで取れるか |
|------|-------------------|
| URL・ファイルサイズ・転送量 | **取れる** |
| ネットワーク往復時間 | **取れる** |
| JS実行時間（CPU時間） | **取れない**（ブラウザ内部のため） |
| 画像デコード時間 | **取れない** |

JS実行時間を自動取得するには Chrome DevTools Protocol（CDP）が必要（Playwright等）。
ただしGBFはログイン・セッション維持が必要なため自動化は複雑。

**現実的な運用**:
- mitmproxyで**ファイルサイズ・転送量**を自動収集（既存の collector.py）
- JS実行時間が必要な場合のみ手動でトレースを保存し、Pythonで解析
