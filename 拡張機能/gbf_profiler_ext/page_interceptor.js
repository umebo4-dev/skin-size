(function () {
  // fetch インターセプト
  const origFetch = window.fetch;
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input
      : (input instanceof Request ? input.url : String(input));
    const resp = await origFetch.apply(this, arguments);
    if (url.includes("/rest/raid/start")) {
      resp.clone().text().then((body) => {
        window.postMessage({ type: "gbf_raid_start", body }, "*");
      });
    }
    return resp;
  };

  // XMLHttpRequest インターセプト
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  const urlMap = new WeakMap();

  XMLHttpRequest.prototype.open = function (method, url) {
    urlMap.set(this, String(url));
    return origOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function () {
    this.addEventListener("load", function () {
      const url = urlMap.get(this) || "";
      if (url.includes("/rest/raid/start")) {
        window.postMessage({ type: "gbf_raid_start", body: this.responseText }, "*");
      }
    });
    return origSend.apply(this, arguments);
  };
})();
