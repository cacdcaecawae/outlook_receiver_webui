const accountList = document.getElementById("accountList");
const startButton = document.getElementById("startButton");
const stopButton = document.getElementById("stopButton");
const copyButton = document.getElementById("copyButton");
const copyAccountButton = document.getElementById("copyAccountButton");
const togglePasswordButton = document.getElementById("togglePasswordButton");
const copyPasswordButton = document.getElementById("copyPasswordButton");
const latestCode = document.getElementById("latestCode");
const stateBadge = document.getElementById("stateBadge");
const totalCount = document.getElementById("totalCount");
const readyCount = document.getElementById("readyCount");
const accountsCountBadge = document.getElementById("accountsCountBadge");
const accountText = document.getElementById("accountText");
const selectedAccountText = document.getElementById("selectedAccountText");
const selectedAccountMeta = document.getElementById("selectedAccountMeta");
const selectedPasswordText = document.getElementById("selectedPasswordText");
const fromText = document.getElementById("fromText");
const subjectText = document.getElementById("subjectText");
const receivedAtText = document.getElementById("receivedAtText");
const hintText = document.getElementById("hintText");
const accountsFileText = document.getElementById("accountsFileText");

let selectedAccountId = null;
let activeAccountId = null;
let currentState = "idle";
let accountsSnapshot = [];
let actionRequestInFlight = false;
let statusRequestInFlight = false;
let refreshTimerId = null;
let passwordVisible = false;

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

function getSelectedAccount() {
  return accountsSnapshot.find((account) => account.id === selectedAccountId) || null;
}

function getActiveAccount() {
  return accountsSnapshot.find((account) => account.id === activeAccountId) || null;
}

function updateStateBadge(state) {
  stateBadge.textContent = state || "idle";
  stateBadge.className = `state-badge ${state || "idle"}`;
}

function updateActionButtons() {
  stopButton.disabled = actionRequestInFlight || currentState !== "listening";
  copyAccountButton.disabled = selectedAccountId === null;
  copyPasswordButton.disabled = selectedAccountId === null;
  togglePasswordButton.disabled = selectedAccountId === null;

  if (selectedAccountId === null) {
    startButton.textContent = "先选账号";
    startButton.disabled = true;
    return;
  }

  const selected = getSelectedAccount();
  if (!selected || !selected.ready) {
    startButton.textContent = "账号不可监听";
    startButton.disabled = true;
    return;
  }

  if (actionRequestInFlight) {
    startButton.textContent = currentState === "listening" ? "切换中..." : "处理中...";
    startButton.disabled = true;
    return;
  }

  startButton.disabled = false;
  if (currentState === "listening" && activeAccountId === selectedAccountId) {
    startButton.textContent = "当前账号监听中";
    startButton.disabled = true;
    return;
  }
  if (currentState === "listening" && activeAccountId !== null && activeAccountId !== selectedAccountId) {
    startButton.textContent = "切换到该账号";
    return;
  }
  startButton.textContent = "开始监听";
}

function updateSelectedAccountPanel() {
  const selected = getSelectedAccount();
  const active = getActiveAccount();

  selectedAccountText.textContent = selected ? selected.email : "-";
  selectedAccountMeta.textContent = active
    ? `当前监听账号: ${active.email}`
    : "当前监听账号: -";

  if (!selected) {
    selectedPasswordText.textContent = "******";
    togglePasswordButton.textContent = "显示密码";
    updateActionButtons();
    return;
  }

  selectedPasswordText.textContent = passwordVisible ? (selected.password || "-") : "******";
  togglePasswordButton.textContent = passwordVisible ? "隐藏密码" : "显示密码";
  updateActionButtons();
}

async function copyText(value, successMessage) {
  if (!value) {
    hintText.textContent = "没有可复制的内容";
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    hintText.textContent = successMessage;
  } catch (error) {
    hintText.textContent = "复制失败，请手动复制";
  }
}

function renderAccountList(accounts, options = {}) {
  const preserveScroll = options.preserveScroll === true;
  const prevScrollTop = preserveScroll ? accountList.scrollTop : 0;
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
    updateSelectedAccountPanel();
    return;
  }

  for (const account of accounts) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "account-item";

    const isSelected = account.id === selectedAccountId;
    const isActive = account.id === activeAccountId && currentState === "listening";

    if (isSelected) {
      item.classList.add("selected");
    }
    if (isActive) {
      item.classList.add("active");
    }
    if (!account.ready) {
      item.classList.add("not-ready");
    }

    const row = document.createElement("div");
    row.className = "account-row";

    const titleBlock = document.createElement("div");
    titleBlock.className = "account-title-block";

    const titleText = document.createElement("strong");
    titleText.className = "account-title";
    titleText.textContent = account.email;

    const meta = document.createElement("span");
    meta.className = "account-meta";
    if (!account.ready) {
      meta.textContent = "缺少 client_id 或 refresh_token";
    } else if (isActive) {
      meta.textContent = "当前正在监听";
    } else if (isSelected) {
      meta.textContent = "已选中，等待切换";
    } else {
      meta.textContent = "点击选中";
    }

    titleBlock.appendChild(titleText);
    titleBlock.appendChild(meta);

    const status = document.createElement("span");
    if (!account.ready) {
      status.className = "account-status blocked";
      status.textContent = "不可监听";
    } else if (isActive) {
      status.className = "account-status listening";
      status.textContent = "监听中";
    } else if (isSelected) {
      status.className = "account-status selected";
      status.textContent = "已选中";
    } else {
      status.className = "account-status ready";
      status.textContent = "可监听";
    }

    row.appendChild(titleBlock);
    row.appendChild(status);
    item.appendChild(row);

    item.addEventListener("click", () => {
      if (!account.ready) {
        return;
      }
      selectedAccountId = account.id;
      passwordVisible = false;
      renderAccountList(accountsSnapshot, { preserveScroll: true });
      hintText.textContent = `已选择 ${account.email}`;
    });

    accountList.appendChild(item);
  }

  if (preserveScroll) {
    accountList.scrollTop = prevScrollTop;
  }
  updateSelectedAccountPanel();
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
  const prevState = currentState;
  const prevActiveAccountId = activeAccountId;
  const prevSelectedAccountId = selectedAccountId;

  currentState = payload.state || "idle";
  activeAccountId = typeof payload.selected_index === "number" ? payload.selected_index : null;

  if (selectedAccountId === null && activeAccountId !== null) {
    selectedAccountId = activeAccountId;
  }

  latestCode.textContent = payload.latest_code || "------";
  accountText.textContent = payload.selected_account || "-";
  fromText.textContent = payload.from || "-";
  subjectText.textContent = payload.subject || "-";
  receivedAtText.textContent = payload.received_at || "-";
  updateStateBadge(currentState);

  const shouldRerenderAccountList =
    prevState !== currentState ||
    prevActiveAccountId !== activeAccountId ||
    prevSelectedAccountId !== selectedAccountId;

  if (shouldRerenderAccountList) {
    renderAccountList(accountsSnapshot, { preserveScroll: true });
  } else {
    updateSelectedAccountPanel();
  }

  if (payload.error) {
    hintText.textContent = payload.error;
  } else if (currentState === "listening") {
    hintText.textContent = payload.latest_code ? "已收到验证码，继续监听中" : "正在监听最新邮件";
  } else if (currentState === "stopped") {
    hintText.textContent = "监听已停止，可切换账号继续";
  } else if (currentState === "idle") {
    hintText.textContent = "等待开始监听";
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

function getRefreshDelay() {
  if (document.hidden) {
    return currentState === "listening" ? 2500 : 4000;
  }
  return currentState === "listening" ? 900 : 1800;
}

function scheduleRefresh(delay = getRefreshDelay()) {
  if (refreshTimerId) {
    clearTimeout(refreshTimerId);
  }
  refreshTimerId = setTimeout(() => {
    void refreshStatus();
  }, delay);
}

async function refreshStatus() {
  if (statusRequestInFlight) {
    return;
  }
  statusRequestInFlight = true;
  try {
    const payload = await request("/api/status");
    renderStatus(payload);
  } catch (error) {
    hintText.textContent = error.message;
  } finally {
    statusRequestInFlight = false;
    scheduleRefresh();
  }
}

async function startListening() {
  if (selectedAccountId === null) {
    hintText.textContent = "先从左侧选择一个账号";
    return;
  }
  if (currentState === "listening" && activeAccountId === selectedAccountId) {
    hintText.textContent = "当前账号已在监听";
    return;
  }
  actionRequestInFlight = true;
  updateActionButtons();
  try {
    const payload = await request("/api/start", {
      method: "POST",
      body: JSON.stringify({ account_id: selectedAccountId }),
    });
    renderStatus(payload);
    const selected = getSelectedAccount();
    hintText.textContent = selected ? `开始监听 ${selected.email}` : "开始监听";
  } catch (error) {
    hintText.textContent = error.message;
  } finally {
    actionRequestInFlight = false;
    updateActionButtons();
    scheduleRefresh(250);
  }
}

async function stopListening() {
  actionRequestInFlight = true;
  updateActionButtons();
  try {
    const payload = await request("/api/stop", {
      method: "POST",
      body: JSON.stringify({}),
    });
    renderStatus(payload);
    hintText.textContent = "当前监听已停止";
  } catch (error) {
    hintText.textContent = error.message;
  } finally {
    actionRequestInFlight = false;
    updateActionButtons();
    scheduleRefresh(250);
  }
}

async function copyLatestCode() {
  const value = latestCode.textContent.trim();
  if (!value || value === "------") {
    hintText.textContent = "当前没有可复制的验证码";
    return;
  }
  await copyText(value, `已复制验证码 ${value}`);
}

async function copySelectedAccount() {
  const selected = getSelectedAccount();
  await copyText(selected?.email, selected ? `已复制账号 ${selected.email}` : "没有已选账号");
}

async function copySelectedPassword() {
  const selected = getSelectedAccount();
  await copyText(selected?.password, selected ? `已复制 ${selected.email} 的密码` : "没有已选账号");
}

function togglePasswordVisibility() {
  passwordVisible = !passwordVisible;
  updateSelectedAccountPanel();
}

startButton.addEventListener("click", startListening);
stopButton.addEventListener("click", stopListening);
copyButton.addEventListener("click", copyLatestCode);
copyAccountButton.addEventListener("click", copySelectedAccount);
copyPasswordButton.addEventListener("click", copySelectedPassword);
togglePasswordButton.addEventListener("click", togglePasswordVisibility);
document.addEventListener("visibilitychange", () => scheduleRefresh(100));

loadAccounts();
void refreshStatus();
