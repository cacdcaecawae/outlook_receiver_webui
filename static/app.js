const accountList = document.getElementById("accountList");
const startButton = document.getElementById("startButton");
const stopButton = document.getElementById("stopButton");
const copyButton = document.getElementById("copyButton");
const latestCode = document.getElementById("latestCode");
const stateBadge = document.getElementById("stateBadge");
const totalCount = document.getElementById("totalCount");
const readyCount = document.getElementById("readyCount");
const accountsCountBadge = document.getElementById("accountsCountBadge");
const accountText = document.getElementById("accountText");
const fromText = document.getElementById("fromText");
const subjectText = document.getElementById("subjectText");
const receivedAtText = document.getElementById("receivedAtText");
const hintText = document.getElementById("hintText");
const accountsFileText = document.getElementById("accountsFileText");

let selectedAccountId = null;
let accountsSnapshot = [];

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

function updateStateBadge(state) {
  stateBadge.textContent = state || "idle";
  stateBadge.className = `state-badge ${state || "idle"}`;
}

function renderAccountList(accounts) {
  accountsSnapshot = accounts;
  accountList.innerHTML = "";

  const firstReady = accounts.find((account) => account.ready);
  if (selectedAccountId === null && firstReady) {
    selectedAccountId = firstReady.id;
  }

  if (!accounts.length) {
    const empty = document.createElement("div");
    empty.className = "account-empty";
    empty.textContent = "当前目录没有可用的 outlook_accounts.txt";
    accountList.appendChild(empty);
    return;
  }

  for (const account of accounts) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "account-item";
    if (account.id === selectedAccountId) {
      item.classList.add("selected");
    }
    if (!account.ready) {
      item.classList.add("disabled");
      item.disabled = true;
    }

    const title = document.createElement("span");
    title.className = "account-title";
    title.textContent = account.email;

    const meta = document.createElement("span");
    meta.className = "account-meta";
    meta.textContent = account.ready ? "OAuth 已就绪" : "缺少 client_id 或 refresh_token";

    item.appendChild(title);
    item.appendChild(meta);
    item.addEventListener("click", () => {
      selectedAccountId = account.id;
      renderAccountList(accountsSnapshot);
      hintText.textContent = `已选择 ${account.email}`;
    });
    accountList.appendChild(item);
  }
}

function renderAccounts(payload) {
  totalCount.textContent = String(payload.count);
  readyCount.textContent = String(payload.ready_count);
  accountsCountBadge.textContent = String(payload.count);
  accountsFileText.textContent = `账号文件: ${payload.accounts_file}`;
  renderAccountList(payload.accounts);
  if (!payload.count) {
    hintText.textContent = `当前目录未读取到账号文件: ${payload.accounts_file}`;
  }
}

function renderStatus(payload) {
  latestCode.textContent = payload.latest_code || "------";
  accountText.textContent = payload.selected_account || "-";
  fromText.textContent = payload.from || "-";
  subjectText.textContent = payload.subject || "-";
  receivedAtText.textContent = payload.received_at || "-";
  updateStateBadge(payload.state);

  if (typeof payload.selected_index === "number") {
    selectedAccountId = payload.selected_index;
    renderAccountList(accountsSnapshot);
  }

  if (payload.error) {
    hintText.textContent = payload.error;
  } else if (payload.state === "listening") {
    hintText.textContent = "正在监听最新邮件";
  } else if (payload.state === "received") {
    hintText.textContent = "已收到最新验证码";
  } else if (payload.state === "stopped") {
    hintText.textContent = "当前监听已停止";
  }
}

async function loadAccounts() {
  try {
    const payload = await request("/api/accounts");
    renderAccounts(payload);
  } catch (error) {
    hintText.textContent = error.message;
  }
}

async function refreshStatus() {
  try {
    const payload = await request("/api/status");
    renderStatus(payload);
  } catch (error) {
    hintText.textContent = error.message;
  }
}

async function startListening() {
  if (selectedAccountId === null) {
    hintText.textContent = "先从左侧选择一个账号";
    return;
  }
  try {
    const payload = await request("/api/start", {
      method: "POST",
      body: JSON.stringify({ account_id: selectedAccountId }),
    });
    renderStatus(payload);
    const selected = accountsSnapshot.find((account) => account.id === selectedAccountId);
    hintText.textContent = selected ? `开始监听 ${selected.email}` : "开始监听";
  } catch (error) {
    hintText.textContent = error.message;
  }
}

async function stopListening() {
  try {
    const payload = await request("/api/stop", {
      method: "POST",
      body: JSON.stringify({}),
    });
    renderStatus(payload);
    hintText.textContent = "当前监听已停止";
  } catch (error) {
    hintText.textContent = error.message;
  }
}

async function copyLatestCode() {
  const value = latestCode.textContent.trim();
  if (!value || value === "------") {
    hintText.textContent = "当前没有可复制的验证码";
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    hintText.textContent = `已复制验证码 ${value}`;
  } catch (error) {
    hintText.textContent = "复制失败，请手动复制";
  }
}

startButton.addEventListener("click", startListening);
stopButton.addEventListener("click", stopListening);
copyButton.addEventListener("click", copyLatestCode);

loadAccounts();
refreshStatus();
setInterval(refreshStatus, 1000);

