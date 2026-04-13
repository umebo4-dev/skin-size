const FLASK_BASE        = "http://127.0.0.1:5000";
const PROFILE_DURATION_SEC = 3;
const ALARM_NAME        = "gbf-stop-trace";

// JS実行時間: npc_3040512000_01 形式の関数名にマッチ
const NPC_FN_PAT  = /\bnpc_(3\d{9})_(\d+)/;
const PHIT_FN_PAT = /\bphit_([a-z]+_\d+|[a-z]+\d+|\d+)(?:_(\d+))?/;


// ─── ファイルサイズ収集用 URLパターン ───────────────────────────────
const CDN_IMG = "prd-game-a-granbluefantasy\\.akamaized\\.net/assets/img_low/sp/";
const CDN_JS  = "prd-game-a-granbluefantasy\\.akamaized\\.net/assets/\\d+/js/cjs/";

const IMG_PATTERNS = {
  sd:       new RegExp(CDN_IMG + "cjs/npc_(\\d+)_(\\d+)(?:_([a-z]))?\\.png"),
  quest:    new RegExp(CDN_IMG + "assets/npc/quest/(?:skin/)?(\\d+)_(\\d+)(?:_(\\d+))?(?:_s(\\d+))?\\.jpg"),
  raid:     new RegExp(CDN_IMG + "assets/npc/raid_normal/(\\d+)_(\\d+)(?:_(\\d+))?\\.jpg"),
  phit_png: new RegExp(CDN_IMG + "cjs/phit_([a-z]+_\\d+|[a-z]+\\d+|\\d+)(?:_(\\d+))?\\.png"),
};
const JS_PATTERNS = {
  cjs_npc:  new RegExp(CDN_JS + "npc_(\\d+)_(\\d+)(?:_s\\d+)?(?:_([a-z]))?\\.js"),
  cjs_phit: new RegExp(CDN_JS + "phit_([a-z]+_\\d+|[a-z]+\\d+|\\d+)(?:_(\\d+))?\\.js"),
};

// バトル中に収集した汎用 phit ID を session に追記
async function trackPhitId(phitId) {
  if (/^3\d{9}$/.test(phitId)) return; // キャラ固有 phit は不要
  const { seenPhitIds } = await chrome.storage.session.get("seenPhitIds");
  const updated = seenPhitIds || {};
  updated[phitId] = true;
  await chrome.storage.session.set({ seenPhitIds: updated });
}

// バトル終了時: weaponCharMap × seenPhitIds で一括紐付け
async function resolvePhitsAtEnd() {
  const { weaponCharMap, seenPhitIds, phitMap } = await chrome.storage.session.get(
    ["weaponCharMap", "seenPhitIds", "phitMap"]
  );
  if (!seenPhitIds) return;
  for (const phitId of Object.keys(seenPhitIds)) {
    // 1) NPC JS から取得した phitMap で解決
    const fromMap = (phitMap || {})[phitId];
    if (fromMap) {
      await sendPhitChar(fromMap.char_id, fromMap.skin_num, phitId);
      continue;
    }
    // 2) パーティ全キャラに紐づける（主人公+計測キャラのみ編成前提）
    // 汎用phitはスキン共通なので skin_num="" で登録
    const allChars = Object.values(weaponCharMap || {}).flat();
    for (const c of allChars) await sendPhitChar(c.char_id, "", phitId);
  }
  await chrome.storage.session.remove("seenPhitIds");
}

function parseImgUrl(url) {
  for (const [type, pat] of Object.entries(IMG_PATTERNS)) {
    const m = pat.exec(url);
    if (!m) continue;
    const filename = url.split("?")[0].split("/").pop();
    if (type === "sd") {
      return { type, char_id: m[1], skin_num: m[2], sheet: m[3] || "", filename };
    } else if (type === "quest") {
      const variant = m[3], sn = m[4];
      const sheet = variant && sn ? `${variant}_s${sn}` : sn ? `s${sn}` : variant || "";
      return { type, char_id: m[1], skin_num: m[2], sheet, filename };
    } else if (type === "raid") {
      return { type, char_id: m[1], skin_num: m[2], sheet: m[3] || "", filename };
    } else if (type === "phit_png") {
      return { type, char_id: m[1], skin_num: m[2] || "", sheet: "", filename };
    }
  }
  return null;
}

// イニシエーター情報から NPC JS URL を取り出す
function extractInitiatorNpc(initiator) {
  if (!initiator) return null;
  // type=parser: initiator.url に直接入っている場合
  if (initiator.url) {
    const m = parseJsUrl(initiator.url);
    if (m?.type === "cjs_npc") return initiator.url;
  }
  // type=script: コールスタックを再帰的に探索
  function searchStack(stack) {
    if (!stack) return null;
    for (const frame of stack.callFrames || []) {
      if (frame.url) {
        const m = parseJsUrl(frame.url);
        if (m?.type === "cjs_npc") return frame.url;
      }
    }
    return searchStack(stack.parent);
  }
  return searchStack(initiator.stack);
}

function parseJsUrl(url) {
  for (const [type, pat] of Object.entries(JS_PATTERNS)) {
    const m = pat.exec(url);
    if (!m) continue;
    const filename = url.split("?")[0].split("/").pop();
    if (type === "cjs_npc") {
      return { type, char_id: m[1], skin_num: m[2], sheet: m[3] || "", filename };
    } else if (type === "cjs_phit") {
      return { type, char_id: m[1], skin_num: m[2] || "", sheet: "", filename };
    }
  }
  return null;
}

async function sendSkinData(data) {
  try {
    await fetch(`${FLASK_BASE}/api/collect_skin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  } catch (e) {
    console.error("[gbf-profiler] sendSkinData failed:", e.message);
  }
}

async function sendPhitChar(char_id, skin_num, phit_id) {
  try {
    await fetch(`${FLASK_BASE}/api/phit_char`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ char_id, skin_num, phit_id }),
    });
  } catch (e) {
    console.error("[gbf-profiler] sendPhitChar failed:", e.message);
  }
}

async function sendCharData(chars) {
  try {
    await fetch(`${FLASK_BASE}/api/collect_char`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chars }),
    });
  } catch (e) {
    console.error("[gbf-profiler] sendCharData failed:", e.message);
  }
}


// ─── 画像サイズ: webRequest fallback（デバッガーアタッチより先に完了する小ファイル対策） ──
chrome.webRequest.onCompleted.addListener(
  async (details) => {
    const { collectMode } = await chrome.storage.local.get("collectMode");
    if ((collectMode || "js") !== "size") return;
    const parsed = parseImgUrl(details.url);
    if (!parsed) return;
    const cl = details.responseHeaders?.find(
      (h) => h.name.toLowerCase() === "content-length"
    );
    if (!cl) return;
    const size_bytes = parseInt(cl.value);
    if (!size_bytes || size_bytes <= 0) return;
    await trackPhitId(parsed.char_id);
    await sendSkinData({ ...parsed, size_bytes, url: details.url });
    console.log("[gbf-profiler] img(webReq):", parsed.filename, size_bytes);
  },
  { urls: ["*://prd-game-a-granbluefantasy.akamaized.net/*"] },
  ["responseHeaders"]
);

// ─── キャラ名: content script からの raidStart メッセージを処理 ────
chrome.runtime.onMessage.addListener(async (msg) => {
  if (msg.type !== "raidStart" || !msg.body) return;
  try {
    const data = JSON.parse(msg.body);
    const params = data?.player?.param || [];
    const chars = [];
    // 武器タイプコード → [{char_id, skin_num}] のマップ
    const weaponCharMap = {};
    for (const p of params) {
      const m = /npc_(\d+)_(\d+)/.exec(p.cjs || "");
      if (!m) continue;
      if (p.name) chars.push({ char_id: m[1], skin_num: m[2], name: p.name });
      const weapons = Array.isArray(p.weapon) ? p.weapon : (p.weapon ? [p.weapon] : []);
      for (const w of weapons) {
        const key = String(w);
        if (!weaponCharMap[key]) weaponCharMap[key] = [];
        weaponCharMap[key].push({ char_id: m[1], skin_num: m[2] });
      }
    }
    if (chars.length) sendCharData(chars);
    await chrome.storage.session.set({ weaponCharMap });
    console.log("[gbf-profiler] weaponCharMap:", weaponCharMap);
  } catch (e) {
    console.error("[gbf-profiler] raidStart parse failed:", e.message);
  }
});

// ─── バトル開始検知 ──────────────────────────────────────────────────
chrome.webRequest.onCompleted.addListener(
  async (details) => {
    if (details.tabId < 0) return;
    const { tracing } = await chrome.storage.session.get("tracing");
    if (tracing) return;
    console.log("[gbf-profiler] battle detected, tab:", details.tabId);
    await startTrace(details.tabId);
  },
  { urls: ["*://game.granbluefantasy.jp/rest/raid/start*"] }
);

chrome.debugger.onDetach.addListener(() => {
  chrome.storage.session.set({ tracing: false, tracingTabId: null });
  pendingRequests = {};
});

// ─── アラーム: プロファイル停止 ──────────────────────────────────────
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== ALARM_NAME) return;
  const { tracingTabId } = await chrome.storage.session.get("tracingTabId");
  console.log("[gbf-profiler] alarm fired, stopping profiler for tab:", tracingTabId);
  if (tracingTabId != null) {
    await stopAndProcess(tracingTabId);
  }
});

// ─── ファイルサイズ: デバッガー Network ドメイン（JS + 画像） ─────────
// requestId → url のマッピング
let pendingRequests = {};

chrome.debugger.onEvent.addListener(async (source, method, params) => {
  if (method === "Network.requestWillBeSent") {
    const url = params.request?.url || "";
    const pJs  = parseJsUrl(url);
    const pImg = parseImgUrl(url);
    if (pJs || pImg) {
      pendingRequests[params.requestId] = url;
      console.log("[gbf-profiler] tracking:", url.split("/").pop());
    }
    // 汎用phit: イニシエーターの NPC JS からキャラを即時特定
    const phitParsed = (pJs?.type  === "cjs_phit"  ? pJs  : null)
                    || (pImg?.type === "phit_png"  ? pImg : null);
    if (phitParsed && !/^3\d{9}$/.test(phitParsed.char_id)) {
      const npcUrl = extractInitiatorNpc(params.initiator);
      if (npcUrl) {
        const npcParsed = parseJsUrl(npcUrl);
        if (npcParsed?.type === "cjs_npc") {
          const { phitMap: existing } = await chrome.storage.session.get("phitMap");
          const phitMap = existing || {};
          if (!phitMap[phitParsed.char_id]) {
            phitMap[phitParsed.char_id] = { char_id: npcParsed.char_id, skin_num: "" };
            await chrome.storage.session.set({ phitMap });
            console.log("[gbf-profiler] phit(initiator):", phitParsed.char_id, "->", npcParsed.char_id);
          }
        }
      }
    }
  }

  if (method === "Network.loadingFinished") {
    const url = pendingRequests[params.requestId];
    if (!url) return;
    delete pendingRequests[params.requestId];

    // ── JSファイル: getResponseBody で実サイズを取得 ──
    const parsedJs = parseJsUrl(url);
    if (parsedJs) {
      try {
        const result = await chrome.debugger.sendCommand(
          { tabId: source.tabId },
          "Network.getResponseBody",
          { requestId: params.requestId }
        );
        const size_bytes = result.base64Encoded
          ? Math.round(result.body.length * 3 / 4)
          : new TextEncoder().encode(result.body).length;

        // NPC JS の内容から phit 参照を抽出して Flask に送信
        if (parsedJs.type === "cjs_npc") {
          const body = result.base64Encoded ? atob(result.body) : result.body;
          const pm = /phit_([a-z]+_\d+|[a-z]+\d+|\d+)/.exec(body);
          if (pm && !/^3\d{9}$/.test(pm[1])) {
            await sendPhitChar(parsedJs.char_id, "", pm[1]);
            const { phitMap: existing } = await chrome.storage.session.get("phitMap");
            const phitMap = existing || {};
            phitMap[pm[1]] = { char_id: parsedJs.char_id, skin_num: "" };
            await chrome.storage.session.set({ phitMap });
            console.log("[gbf-profiler] phit from JS:", pm[1], "->", parsedJs.char_id);
          } else {
            console.log("[gbf-profiler] NPC JS no phit ref:", parsedJs.filename, "b64:", result.base64Encoded);
          }
        }

        let resolved = parsedJs;
        if (parsedJs.type === "cjs_phit") {
          await trackPhitId(parsedJs.char_id);
        }
        await sendSkinData({ ...resolved, size_bytes, url });
        console.log("[gbf-profiler] JS size:", resolved.filename, size_bytes);
      } catch (e) {
        console.error("[gbf-profiler] getResponseBody failed:", e.message);
      }
      return;
    }

    // ── 画像ファイル: encodedDataLength（HTTP/2 でも確実に取れる）──
    const parsedImg = parseImgUrl(url);
    if (parsedImg) {
      const size_bytes = params.encodedDataLength;
      if (!size_bytes || size_bytes <= 0) return;
      let resolved = parsedImg;
      if (parsedImg.type === "phit_png") {
        await trackPhitId(parsedImg.char_id);
      }
      await sendSkinData({ ...resolved, size_bytes, url });
      console.log("[gbf-profiler] img size:", resolved.type, resolved.filename, size_bytes);
    }
  }
});

// ─── プロファイラー開始 ───────────────────────────────────────────────
async function startTrace(tabId) {
  try {
    const { collectMode } = await chrome.storage.local.get("collectMode");
    const mode = collectMode || "js";
    await chrome.storage.session.set({ tracing: true, tracingTabId: tabId });
    await chrome.debugger.attach({ tabId }, "1.3");
    if (mode === "js") {
      await chrome.debugger.sendCommand({ tabId }, "Profiler.enable");
      await chrome.debugger.sendCommand({ tabId }, "Profiler.setSamplingInterval", { interval: 100 });
      await chrome.debugger.sendCommand({ tabId }, "Profiler.start");
    } else {
      await chrome.debugger.sendCommand({ tabId }, "Network.enable");
    }
    console.log("[gbf-profiler] mode:", mode, "started");
    chrome.alarms.create(ALARM_NAME, { delayInMinutes: PROFILE_DURATION_SEC / 60 });
  } catch (e) {
    console.error("[gbf-profiler] startTrace failed:", e.message);
    await cleanup(tabId);
  }
}

// ─── プロファイラー停止・処理 ─────────────────────────────────────────
async function stopAndProcess(tabId) {
  try {
    const { collectMode } = await chrome.storage.local.get("collectMode");
    const mode = collectMode || "js";
    if (mode === "js") {
      const result = await chrome.debugger.sendCommand({ tabId }, "Profiler.stop");
      const p = result.profile;
      console.log("[gbf-profiler] profile received, nodes:", p.nodes.length, "samples:", p.samples.length);
      await processProfile(p, tabId);
    }
    // サイズ・JS問わず、収集した phit を一括解決
    await resolvePhitsAtEnd();
  } catch (e) {
    console.error("[gbf-profiler] stopAndProcess failed:", e.message);
  } finally {
    await cleanup(tabId);
  }
}

// ─── CPUプロファイル解析 ──────────────────────────────────────────────
async function processProfile(profile, tabId) {
  const parentId = {};
  const nodeKey  = {};

  for (const node of profile.nodes) {
    const fn  = node.callFrame.functionName || "";
    const url = node.callFrame.url || "";
    const src = fn || url;

    let m;
    m = NPC_FN_PAT.exec(src);
    if (m) {
      nodeKey[node.id] = `npc:${m[1]}:${m[2]}`;
    } else {
      m = PHIT_FN_PAT.exec(src);
      if (m) {
        nodeKey[node.id] = `phit:${m[1]}:${m[2] || ""}`;
      }
    }

    for (const childId of (node.children || [])) {
      parentId[childId] = node.id;
    }
  }

  const keyTime = {};
  for (let i = 0; i < profile.samples.length; i++) {
    const delta   = profile.timeDeltas[i] || 0;
    const matched = new Set();
    let id = profile.samples[i];
    while (id) {
      const key = nodeKey[id];
      if (key && !matched.has(key)) {
        keyTime[key] = (keyTime[key] || 0) + delta;
        matched.add(key);
      }
      id = parentId[id];
    }
  }

  console.log("[gbf-profiler] keyTime:", keyTime);

  const entries = [];
  for (const [key, timeUs] of Object.entries(keyTime)) {
    const [type, charId, skinNum] = key.split(":");
    let entry = { char_id: charId, skin_num: skinNum, type, exec_time_us: Math.round(timeUs) };
    if (type === "phit") {
      await trackPhitId(charId);
    }
    entries.push(entry);
  }

  console.log("[gbf-profiler] entries:", entries);
  await chrome.storage.local.set({
    lastCapture: { time: new Date().toLocaleTimeString("ja-JP"), entries },
  });

  for (const entry of entries) {
    try {
      await fetch(`${FLASK_BASE}/api/js_exec`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entry),
      });
    } catch (e) {
      console.error("[gbf-profiler] send js_exec failed:", e.message);
    }
  }
}

// ─── クリーンアップ ───────────────────────────────────────────────────
async function cleanup(tabId) {
  chrome.alarms.clear(ALARM_NAME);
  pendingRequests = {};
  try { await chrome.debugger.detach({ tabId }); } catch (_) {}
  chrome.storage.session.set({ tracing: false, tracingTabId: null });
}

// ─── ポップアップ状態クエリ ───────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "getState") {
    Promise.all([
      chrome.storage.session.get("tracing"),
      chrome.storage.local.get("lastCapture"),
    ]).then(([sess, local]) => {
      reply({ tracing: !!sess.tracing, lastCapture: local.lastCapture || null });
    });
    return true;
  }
});
