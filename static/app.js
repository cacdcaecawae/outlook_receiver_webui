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
let activeAccountId = null;
let currentState = "idle";
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

function updateStartButtonState() {
  if (selectedAccountId === null) {
    startButton.textContent = "先选账号";
    startButton.disabled = true;
    return;
  }
  const selected = accountsSnapshot.find((account) => account.id === selectedAccountId);
  if (!selected || !selected.ready) {
    startButton.textContent = "账号不可监听";
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
    updateStartButtonState();
    return;
  }

  for (const account of accounts) {
    const item = document.createElement("article");
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

    const head = document.createElement("div");
    head.className = "account-head";

    const title = document.createElement("div");
    title.className = "account-title-block";

    const titleText = document.createElement("strong");
    titleText.className = "account-title";
    titleText.textContent = account.email;

    const meta = document.createElement("span");
    meta.className = "account-meta";
    meta.textContent = account.ready ? "OAuth 已就绪" : "缺少 client_id 或 refresh_token";

    title.appendChild(titleText);
    title.appendChild(meta);

    const status = document.createElement("span");
    if (!account.ready) {
      status.className = "account-status blocked";
      status.textContent = "不可监听";
    } else if (isActive) {
      status.className = "account-status listening";
      status.textContent = "监听中";
    } else {
      status.className = "account-status ready";
      status.textContent = "可监听";
    }

    head.appendChild(title);
    head.appendChild(status);

    const emailRow = document.createElement("button");
    emailRow.type = "button";
    emailRow.className = "account-secret";
    emailRow.title = "点击复制账号";
    emailRow.innerHTML = `<span class="account-secret-label">账号</span><code class="account-secret-value">${account.email}</code>`;
    emailRow.addEventListener("click", (event) => {
      event.stopPropagation();
      copyText(account.email, `已复制账号 ${account.email}`);
    });

    const passwordRow = document.createElement("button");
    passwordRow.type = "button";
    passwordRow.className = "account-secret";
    passwordRow.title = "点击复制密码";
    passwordRow.innerHTML = `<span class="account-secret-label">密码</span><code class="account-secret-value">${account.password || "-"}</code>`;
    passwordRow.addEventListener("click", (event) => {
      event.stopPropagation();
      copyText(account.password, `已复制 ${account.email} 的密码`);
    });

    const actions = document.createElement("div");
    actions.className = "account-actions";

    const selectButton = document.createElement("button");
    selectButton.type = "button";
    selectButton.className = "account-action account-select";
    selectButton.textContent = isSelected ? "已选中" : "选中监听";
    selectButton.disabled = !account.ready;
    selectButton.addEventListener("click", (event) => {
      event.stopPropagation();
      selectedAccountId = account.id;
      renderAccountList(accountsSnapshot, { preserveScroll: true });
      hintText.textContent = `已选择 ${account.email}`;
    });

    item.addEventListener("click", () => {
      if (!account.ready) {
        return;
      }
      selectedAccountId = account.id;
      renderAccountList(accountsSnapshot, { preserveScroll: true });
      hintText.textContent = `已选择 ${account.email}`;
    });

    actions.appendChild(selectButton);

    item.appendChild(head);
    item.appendChild(emailRow);
    item.appendChild(passwordRow);
    item.appendChild(actions);
    accountList.appendChild(item);
  }

  if (preserveScroll) {
    accountList.scrollTop = prevScrollTop;
  }
  updateStartButtonState();
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
    updateStartButtonState();
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
  if (currentState === "listening" && activeAccountId === selectedAccountId) {
    hintText.textContent = "当前账号已在监听";
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
  await copyText(value, `已复制验证码 ${value}`);
}

startButton.addEventListener("click", startListening);
stopButton.addEventListener("click", stopListening);
copyButton.addEventListener("click", copyLatestCode);

loadAccounts();
refreshStatus();
setInterval(refreshStatus, 1000);
