const MODE_NOTE = {
  js:   "キャッシュあり状態でバトル開始。\nJS実行時間を計測します。",
  size: "キャッシュクリア後にバトル開始。\n画像・JSファイルサイズを収集します。",
};

function applyModeUI(mode) {
  document.getElementById("btnJs").classList.toggle("active",   mode === "js");
  document.getElementById("btnSize").classList.toggle("active", mode === "size");
  document.getElementById("modeNote").textContent = MODE_NOTE[mode] || "";
}

function setMode(mode) {
  chrome.storage.local.set({ collectMode: mode });
  applyModeUI(mode);
}

document.getElementById("btnJs").addEventListener("click",   () => setMode("js"));
document.getElementById("btnSize").addEventListener("click", () => setMode("size"));

// 初期状態を取得
chrome.storage.local.get(["collectMode", "lastCapture"], (local) => {
  const mode = local.collectMode || "js";
  applyModeUI(mode);

  const cap = local.lastCapture;
  if (cap && cap.entries?.length > 0) {
    document.getElementById("captureTime").textContent = cap.time;
    document.getElementById("entries").innerHTML = cap.entries
      .sort((a, b) => b.exec_time_us - a.exec_time_us)
      .map((e) => {
        const label = `${e.type} ${e.char_id}${e.skin_num ? "_" + e.skin_num : ""}`;
        const ms    = (e.exec_time_us / 1000).toFixed(2) + " ms";
        return `<div class="entry">${label}<span class="time-val">${ms}</span></div>`;
      })
      .join("");
  }
});

chrome.runtime.sendMessage({ type: "getState" }, (state) => {
  const dot        = document.getElementById("dot");
  const statusText = document.getElementById("statusText");
  if (state?.tracing) {
    dot.className       = "dot active";
    statusText.textContent = "計測中...";
  } else {
    dot.className       = "dot off";
    statusText.textContent = "待機中（バトル開始で自動計測）";
  }
});
