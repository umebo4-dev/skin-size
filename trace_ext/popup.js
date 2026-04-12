const statusEl  = document.getElementById("status");
const counterEl = document.getElementById("counter");
const resetBtn  = document.getElementById("resetBtn");

function updateUI(status) {
  statusEl.textContent = status === "tracing" ? "トレース中..." : "待機中";
  statusEl.className   = status === "tracing" ? "tracing" : "idle";
}

function updateCounter(count) {
  counterEl.textContent = `${count} / 10`;
}

chrome.storage.session.get(["status", "traceCount"], ({ status, traceCount }) => {
  updateUI(status || "idle");
  updateCounter(traceCount || 0);
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "session") return;
  if (changes.status)     updateUI(changes.status.newValue);
  if (changes.traceCount) updateCounter(changes.traceCount.newValue);
});

resetBtn.addEventListener("click", () => {
  chrome.storage.local.set({ traceCount: 0 });
  chrome.storage.session.set({ traceCount: 0 });
  updateCounter(0);
});
