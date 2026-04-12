const QUEST_START_PATTERN  = "*://game.granbluefantasy.jp/rest/raid/start*";
const QUEST_RESULT_PATTERN = "*://game.granbluefantasy.jp/*result*";
const COOLDOWN_MS = 10;
const NOTIFY_COUNT = 10;

// 10枚目のダウンロード完了を待ってから履歴削除
let eraseAfterDownloadId = null;

chrome.downloads.onChanged.addListener((delta) => {
  if (delta.id === eraseAfterDownloadId && delta.state?.current === "complete") {
    eraseAfterDownloadId = null;
    chrome.downloads.erase({});
    chrome.storage.local.set({ traceCount: 0 });
    chrome.storage.session.set({ traceCount: 0 });
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon.png",
      title: "GBF Trace Capture",
      message: `${NOTIFY_COUNT}件のトレースを保存しました。インポートを実行してください。`,
    });
  }
});

// ── メモリ上の状態（SWが生きている間だけ有効） ──
let tracing = false;
let traceEvents = [];
let cooldownUntil = 0;

// ── クエスト入場検知 ──
chrome.webRequest.onCompleted.addListener(
  (details) => {
    if (!tracing && Date.now() >= cooldownUntil) {
      startTrace(details.tabId);
    }
  },
  { urls: [QUEST_START_PATTERN] }
);

// ── クエストリザルト検知 → 停止 ──
chrome.webRequest.onCompleted.addListener(
  () => {
    if (tracing) stopTrace();
  },
  { urls: [QUEST_RESULT_PATTERN] }
);

// ── デバッガーイベント ──
chrome.debugger.onEvent.addListener((source, method, params) => {
  if (method === "Tracing.dataCollected") {
    traceEvents.push(...(params.value || []));
  }
  if (method === "Tracing.tracingComplete") {
    saveTrace();
  }
});


async function startTrace(tabId) {
  try {
    tracing = true;
    traceEvents = [];

    await chrome.debugger.attach({ tabId }, "1.3");
    await chrome.debugger.sendCommand({ tabId }, "Tracing.start", {
      categories: "devtools.timeline",
    });

    // tabId を session に保存（SW再起動後も停止できるように）
    chrome.storage.session.set({ status: "tracing", tracingTabId: tabId });
  } catch (e) {
    console.error("[GBF Trace] startTrace 失敗:", e);
    await cleanup();
  }
}

async function stopTrace() {
  // SW再起動でメモリが消えていても session から tabId を復元
  const { tracingTabId } = await chrome.storage.session.get("tracingTabId");
  if (!tracingTabId) return;
  try {
    await chrome.debugger.sendCommand({ tabId: tracingTabId }, "Tracing.end");
    // tracingComplete イベントで saveTrace が呼ばれる
  } catch (e) {
    console.error("[GBF Trace] stopTrace 失敗:", e);
    await cleanup();
  }
}

async function saveTrace() {
  const { tracingTabId } = await chrome.storage.session.get("tracingTabId");
  const timestamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace("T", "_")
    .slice(0, 15);
  const filename = `trace_${timestamp}.json`;
  const json = JSON.stringify({ traceEvents });

  try {
    const base64 = btoa(unescape(encodeURIComponent(json)));
    await chrome.downloads.download({
      url: `data:application/json;base64,${base64}`,
      filename,
      saveAs: false,
    });

    const { traceCount = 0 } = await chrome.storage.local.get("traceCount");
    const newCount = traceCount + 1;
    await chrome.storage.local.set({ traceCount: newCount });
    chrome.storage.session.set({ traceCount: newCount });

    if (newCount >= NOTIFY_COUNT) {
      // 10枚目のダウンロード完了後に履歴削除・通知（onChanged で処理）
      eraseAfterDownloadId = downloadId;
    }
  } catch (e) {
    console.error("[GBF Trace] saveTrace 失敗:", e);
  }

  await cleanup();
}

async function cleanup() {
  const { tracingTabId } = await chrome.storage.session.get("tracingTabId");
  if (tracingTabId) {
    chrome.debugger.detach({ tabId: tracingTabId }).catch(() => {});
  }
  tracing = false;
  traceEvents = [];
  cooldownUntil = Date.now() + COOLDOWN_MS;
  chrome.storage.session.set({ status: "idle", tracingTabId: null });
}
