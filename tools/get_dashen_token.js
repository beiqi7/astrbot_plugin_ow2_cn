/**
 * 网易大神 getReportToken 取 token 脚本（浏览器控制台用）
 *
 * 用法：
 *   1. 在浏览器里登录 https://ds.163.com/ （需要账号已绑定战网）；
 *   2. 打开 F12 → Console，把本文件内容整段粘贴进去，按回车；
 *   3. 把 `YOUR_ROLE_ID` 换成你自己的守望先锋角色 id（获取方式见插件 README）；
 *   4. 脚本会打印 role_id / token 和一段可直接填进插件配置的 JSON。
 *
 * 为什么需要这个脚本：上游 datamsapi 只认 (role_id, token)，而这个 token 必须由
 * 网易大神页面自带的 `sig` 模块对请求体签名后换取。注意官方现在把该模块打成了
 * webpack chunk，`window.sig` 已不是全局变量，网上流传的旧脚本会直接报
 * "Cannot read properties of undefined" —— 本脚本会自己把模块加载进来，
 * 并且自动从 bootstrap.js 里解析资源哈希，官方发版后一般不用改。
 *
 * 本脚本只在你自己的浏览器里运行，凭据不会离开你的机器。
 */
(async () => {
  // ==== 需要你填写的地方 ====
  const ROLE_ID = "YOUR_ROLE_ID";
  const CLIENT_TYPE = "61";
  const SIG_CDN = "https://g.166.net/opd/latest/sig/";
  // ==========================

  const log = (...args) => console.log("[ow2cn]", ...args);

  const textAt = async (url) => {
    const resp = await fetch(url, { credentials: "omit" });
    if (!resp.ok) throw new Error(`${url} -> HTTP ${resp.status}`);
    return resp.text();
  };

  // 1) 加载签名模块（自动解析 chunk / wasm 哈希，避免写死版本号）
  const bootstrap = await textAt(SIG_CDN + "bootstrap.js");
  (0, eval)(bootstrap);

  const chunkHashes = [];
  for (const match of bootstrap.matchAll(/\{(\d+):"([0-9a-f]{8,})"\}/g)) {
    const tail = bootstrap.slice(match.index, match.index + 80);
    if (tail.includes(".module.wasm")) continue; // wasm 交给运行时自己拉取
    chunkHashes.push(match[2]);
  }
  for (const hash of [...new Set(chunkHashes)]) {
    try {
      (0, eval)(await textAt(`${SIG_CDN}${hash}.js`));
    } catch (err) {
      log("chunk 预加载失败（可忽略，若后面报错再看）：", hash, String(err));
    }
  }
  const sig = await window.sig.default();
  log("签名模块版本:", sig.get_version ? sig.get_version() : "unknown");

  // 2) 签名并换 token
  const body = JSON.stringify({ appKey: "bn", roleId: ROLE_ID, server: "1", source: 1, type: "yearly" });
  const sign = JSON.parse(sig.gen_sign(body));
  const cookie = (name) =>
    (document.cookie.split("; ").find((row) => row.startsWith(name + "=")) || "")
      .split("=")
      .slice(1)
      .join("=");

  const resp = await fetch("https://inf.ds.163.com/v1/web/game/report/getReportToken", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json;charset=UTF-8",
      "GL-ClientType": CLIENT_TYPE,
      "GL-DeviceId": localStorage.getItem("ns-client-id") || localStorage.getItem("ds-website-uuid") || "",
      "GL-Uid": cookie("GOD_UUID"),
      "GL-X-XSRF-TOKEN": cookie("GL-XSRF-TOKEN"),
      "GL-CheckSum": sign.sign,
      "GL-Nonce": String(sign.timestamp),
    },
    body,
  });
  const payload = await resp.json();

  if (payload.token || (payload.result && payload.result.token)) {
    const token = payload.token || payload.result.token;
    log("✅ 成功");
    console.log(
      JSON.stringify([{ name: "primary", role_id: ROLE_ID, token }], null, 2),
    );
    log("把上面这段 JSON 填进插件配置的「网易大神 → accounts」即可。token 会过期，失效后重跑本脚本。");
    return;
  }

  const hints = {
    825: "签名无效：roleId 不是你账号下真实绑定的角色 id，请核对；也请确认该大神账号已绑定战网。",
    803: "参数错误：body 缺字段，确认 ROLE_ID 已替换。",
    873: "会话过期：重新登录大神再跑一次。",
  };
  log(`❌ 失败 code=${payload.code} errmsg=${payload.errmsg || ""}`);
  if (hints[payload.code]) log(hints[payload.code]);
  console.log(payload);
})();
