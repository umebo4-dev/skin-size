// ページコンテキストにスクリプトを注入してXHR/fetchをインターセプト
const script = document.createElement("script");
script.src = chrome.runtime.getURL("page_interceptor.js");
(document.head || document.documentElement).appendChild(script);
script.remove();

// ページからのメッセージをbackgroundに転送
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  if (event.data?.type === "gbf_raid_start") {
    chrome.runtime.sendMessage({ type: "raidStart", body: event.data.body });
  }
});
