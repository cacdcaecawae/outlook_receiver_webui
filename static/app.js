function app() {
  return {
    state: "idle",
    selectedGroupId: "",
    selectedAccountId: null,
    activeAccountId: null,
    passwordVisible: false,

    groups: [],
    accounts: [],
    accountsFile: "",
    groupsFile: "",

    latestCode: "",
    latestSubject: "",
    latestFrom: "",
    latestFolder: "",
    latestReceivedAt: "",
    latestMessageKey: "",
    latestMailEventId: 0,
    latestStatusEventId: 0,

    hintText: "等待开始监听",
    mailAlertText: "",
    mailAlertAt: "",
    streamState: "idle",
    baseTitle: document.title,
    eventSource: null,
    pollTimer: null,
    groupsSaveTimer: null,
    pendingSaveToken: 0,
    saveInFlight: false,
    lastHandledMailEventId: 0,
    titleTimer: null,
    audioContext: null,

    stats: {
      total: 0,
      ready: 0,
      groups: 0,
    },

    tagOptions: [
      { value: "unmarked", label: "未标记" },
      { value: "mother", label: "母号" },
      { value: "child", label: "子号" },
      { value: "banned", label: "封号" },
    ],

    async init() {
      this.baseTitle = document.title;
      await this.loadAccounts();
      await this.refreshStatus();
      this.startEventStream();
    },

    get currentGroup() {
      return this.groups.find((group) => group.id === this.selectedGroupId) || null;
    },

    get selectedAccount() {
      return this.accounts.find((account) => account.id === this.selectedAccountId) || null;
    },

    get canStart() {
      const account = this.selectedAccount;
      const isListenable = !!(
        account &&
        (account.listenable ?? (account.ready && account.tag !== "banned"))
      );
      return !!(
        account &&
        isListenable &&
        !(this.state === "listening" && this.activeAccountId === this.selectedAccountId)
      );
    },

    get streamLabel() {
      const labels = {
        idle: "未连接",
        connecting: "实时推送连接中",
        connected: "实时推送已连接",
        reconnecting: "实时推送重连中",
        fallback: "轮询模式",
      };
      return labels[this.streamState] || labels.idle;
    },

    get streamClass() {
      const classes = {
        idle: "bg-slate-100 text-slate-600",
        connecting: "bg-sky-100 text-sky-700",
        connected: "bg-emerald-100 text-emerald-700",
        reconnecting: "bg-amber-100 text-amber-700",
        fallback: "bg-slate-100 text-slate-600",
      };
      return classes[this.streamState] || classes.idle;
    },

    get latestMailSummary() {
      return [this.latestFrom, this.latestSubject, this.latestFolder, this.latestReceivedAt]
        .filter(Boolean)
        .join(" · ");
    },

    async request(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Request failed");
      }
      return data;
    },

    hydrateAccounts(data) {
      this.groups = data.account_groups || [];
      this.accounts = this.groups.flatMap((group) => group.accounts);
      this.accountsFile = data.accounts_file || "";
      this.groupsFile = data.groups_file || "";
      this.updateStats();
      this.ensureSelection();
    },

    applyStatus(data, options = {}) {
      const preserveHint = !!options.preserveHint;
      this.state = data.is_listening ? "listening" : data.state || "idle";
      this.latestCode = data.latest_code || "";
      this.latestSubject = data.subject || "";
      this.latestFrom = data.from || "";
      this.latestFolder = data.folder || "";
      this.latestReceivedAt = data.received_at || "";
      this.latestMessageKey = data.latest_message_key || "";
      this.latestMailEventId = Number(data.mail_event_id || 0);
      this.latestStatusEventId = Number(data.status_event_id || 0);
      this.activeAccountId =
        typeof data.active_account_id === "number"
          ? data.active_account_id
          : typeof data.selected_index === "number"
            ? data.selected_index
            : null;

      if (data.error) {
        this.hintText = data.error;
        return;
      }

      if (!preserveHint) {
        this.hintText = this.getDefaultHint();
      }
    },

    getDefaultHint() {
      if (this.state === "listening") {
        return this.latestCode ? "已收到验证码，继续监听中" : "正在监听最新邮件";
      }
      if (this.state === "stopped") {
        return "监听已停止，可切换账号继续";
      }
      if (this.state === "error") {
        return "监听发生错误，请检查账号状态";
      }
      return "等待开始监听";
    },

    async loadAccounts() {
      try {
        const data = await this.request("/api/accounts");
        this.hydrateAccounts(data);
      } catch (error) {
        this.hintText = error.message;
      }
    },

    async refreshStatus(options = {}) {
      try {
        const data = await this.request("/api/status");
        this.applyStatus(data, options);
      } catch (error) {
        this.hintText = error.message;
      }
    },

    startEventStream() {
      if (typeof window.EventSource !== "function") {
        this.streamState = "fallback";
        this.startPolling();
        return;
      }

      if (this.eventSource) {
        this.eventSource.close();
      }

      this.streamState = "connecting";
      const source = new EventSource("/api/events");
      this.eventSource = source;

      source.onopen = () => {
        this.streamState = "connected";
        this.stopPolling();
      };

      source.addEventListener("status", (event) => {
        this.streamState = "connected";
        this.applyStatus(JSON.parse(event.data));
      });

      source.addEventListener("mail", (event) => {
        this.streamState = "connected";
        const data = JSON.parse(event.data);
        this.applyStatus(data, { preserveHint: true });
        this.handleMailEvent(data);
      });

      source.onerror = () => {
        this.streamState = "reconnecting";
        this.startPolling();
      };
    },

    startPolling() {
      if (this.pollTimer) {
        return;
      }

      this.pollTimer = window.setInterval(() => {
        this.refreshStatus({ preserveHint: this.state === "listening" && !!this.latestCode });
      }, 4000);
    },

    stopPolling() {
      if (!this.pollTimer) {
        return;
      }

      window.clearInterval(this.pollTimer);
      this.pollTimer = null;
    },

    handleMailEvent(data) {
      const eventId = Number(data.mail_event_id || 0);
      if (!eventId || eventId === this.lastHandledMailEventId) {
        return;
      }

      this.lastHandledMailEventId = eventId;
      const summary = [];
      if (data.latest_code) {
        summary.push(`验证码 ${data.latest_code}`);
      }
      if (data.subject) {
        summary.push(data.subject);
      }
      if (data.from) {
        summary.push(data.from);
      }

      this.mailAlertText = summary.join(" · ") || "收到新邮件";
      this.mailAlertAt = data.received_at || "";
      this.hintText = data.latest_code ? `收到新邮件验证码 ${data.latest_code}` : "收到新邮件";
      this.flashTitle(data.latest_code || "新邮件");
      this.playAlertTone();
      this.showDesktopNotification(data);
    },

    flashTitle(value) {
      document.title = `[新邮件] ${value} - ${this.baseTitle}`;
      if (this.titleTimer) {
        window.clearTimeout(this.titleTimer);
      }
      this.titleTimer = window.setTimeout(() => {
        document.title = this.baseTitle;
      }, 12000);
    },

    playAlertTone() {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) {
        return;
      }

      try {
        if (!this.audioContext) {
          this.audioContext = new AudioContextClass();
        }

        const now = this.audioContext.currentTime;
        const gainNode = this.audioContext.createGain();
        const oscillator = this.audioContext.createOscillator();
        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(880, now);
        gainNode.gain.setValueAtTime(0.0001, now);
        gainNode.gain.exponentialRampToValueAtTime(0.08, now + 0.02);
        gainNode.gain.exponentialRampToValueAtTime(0.0001, now + 0.24);
        oscillator.connect(gainNode);
        gainNode.connect(this.audioContext.destination);
        oscillator.start(now);
        oscillator.stop(now + 0.24);
      } catch {
        // Best-effort only.
      }
    },

    showDesktopNotification(data) {
      if (typeof window.Notification !== "function") {
        return;
      }
      if (window.Notification.permission !== "granted") {
        return;
      }

      const body = [data.latest_code ? `验证码 ${data.latest_code}` : "", data.subject || data.from || ""]
        .filter(Boolean)
        .join(" · ");

      try {
        new window.Notification("收到新邮件", { body });
      } catch {
        // Ignore browser notification failures.
      }
    },

    updateStats() {
      this.stats.total = this.accounts.length;
      this.stats.ready = this.accounts.filter(
        (account) => account.listenable ?? (account.ready && account.tag !== "banned"),
      ).length;
      this.stats.groups = this.groups.length;
    },

    ensureSelection() {
      if (!this.groups.length) {
        this.selectedGroupId = "";
        this.selectedAccountId = null;
        return;
      }

      if (!this.selectedGroupId || !this.groups.find((group) => group.id === this.selectedGroupId)) {
        this.selectedGroupId = this.groups[0].id;
      }

      const group = this.currentGroup;
      if (!group || !group.accounts.length) {
        this.selectedAccountId = null;
        return;
      }

      if (!this.selectedAccountId || !group.accounts.find((account) => account.id === this.selectedAccountId)) {
        this.selectedAccountId = group.accounts[0].id;
      }
    },

    selectGroup(groupId) {
      this.selectedGroupId = groupId;
      const group = this.currentGroup;
      this.selectedAccountId = group?.accounts[0]?.id ?? null;
      this.passwordVisible = false;
    },

    selectAccount(accountId) {
      this.selectedAccountId = accountId;
      this.passwordVisible = false;
    },

    async startListening() {
      if (!this.selectedAccountId) {
        this.hintText = "请先选择一个账号";
        return;
      }

      if (this.state === "listening" && this.activeAccountId === this.selectedAccountId) {
        this.hintText = "当前账号已在监听";
        return;
      }

      if (!this.canStart) {
        this.hintText = "当前账号不可监听";
        return;
      }

      try {
        const data = await this.request("/api/start", {
          method: "POST",
          body: JSON.stringify({ account_id: this.selectedAccountId }),
        });
        this.applyStatus(data, { preserveHint: true });
        this.hintText = `开始监听 ${this.selectedAccount?.email || ""}`.trim();
      } catch (error) {
        this.hintText = error.message;
      }
    },

    async stopListening() {
      try {
        const data = await this.request("/api/stop", {
          method: "POST",
          body: JSON.stringify({}),
        });
        this.applyStatus(data, { preserveHint: true });
        this.hintText = "监听已停止";
      } catch (error) {
        this.hintText = error.message;
      }
    },

    async copyCode() {
      if (!this.latestCode || this.latestCode === "------") {
        this.hintText = "当前没有可复制的验证码";
        return;
      }
      try {
        await navigator.clipboard.writeText(this.latestCode);
        this.hintText = `已复制验证码 ${this.latestCode}`;
      } catch {
        this.hintText = "复制失败，请手动复制";
      }
    },

    async copyAccount() {
      if (!this.selectedAccount) {
        return;
      }
      try {
        await navigator.clipboard.writeText(this.selectedAccount.email);
        this.hintText = `已复制账号 ${this.selectedAccount.email}`;
      } catch {
        this.hintText = "复制失败，请手动复制";
      }
    },

    async copyPassword() {
      if (!this.selectedAccount) {
        return;
      }
      try {
        await navigator.clipboard.writeText(this.selectedAccount.password);
        this.hintText = `已复制 ${this.selectedAccount.email} 的密码`;
      } catch {
        this.hintText = "复制失败，请手动复制";
      }
    },

    togglePassword() {
      this.passwordVisible = !this.passwordVisible;
    },

    syncAccountFlags(account) {
      const isReady = !!account.ready;
      const isBanned = account.tag === "banned";
      account.listenable = isReady && !isBanned;
      account.disabled_reason = isBanned ? "banned" : isReady ? null : "missing_credentials";
    },

    setTag(email, tag) {
      const group = this.groups.find((entry) => entry.accounts.some((account) => account.email === email));
      const account = group?.accounts.find((entry) => entry.email === email);
      if (!group || !account) {
        return;
      }

      if (account.tag === tag) {
        return;
      }

      if (tag === "mother") {
        group.accounts.forEach((entry) => {
          if (entry.email !== email && entry.tag === "mother") {
            entry.tag = "child";
            this.syncAccountFlags(entry);
          }
        });
      }

      account.tag = tag;
      this.syncAccountFlags(account);
      this.updateStats();
      const shouldStopActiveListener = tag === "banned" && this.state === "listening" && this.activeAccountId === account.id;
      this.queueGroupsSave(
        shouldStopActiveListener ? "封号已标记，正在停止监听..." : "标签已更新",
        { immediate: shouldStopActiveListener },
      );
    },

    editNote(email) {
      const group = this.groups.find((entry) => entry.accounts.some((account) => account.email === email));
      const account = group?.accounts.find((entry) => entry.email === email);
      if (!account) {
        return;
      }

      const note = window.prompt("请输入备注", account.note || "");
      if (note === null) {
        return;
      }

      account.note = note.trim();
      this.queueGroupsSave("备注已保存");
    },

    queueGroupsSave(message = "分组已保存", options = {}) {
      const immediate = !!options.immediate;
      const token = ++this.pendingSaveToken;
      if (this.groupsSaveTimer) {
        window.clearTimeout(this.groupsSaveTimer);
        this.groupsSaveTimer = null;
      }
      this.hintText = "正在保存分组...";
      if (immediate) {
        this.persistGroups(token, message);
        return;
      }
      this.groupsSaveTimer = window.setTimeout(() => {
        this.groupsSaveTimer = null;
        this.persistGroups(token, message);
      }, 180);
    },

    buildGroupsPayload() {
      return {
        groups: this.groups.map((group) => ({
          id: group.id,
          name: group.name,
          accounts: group.accounts.map((account) => ({
            email: account.email,
            tag: account.tag,
            note: account.note || "",
          })),
        })),
      };
    },

    async persistGroups(token, message = "分组已保存") {
      this.saveInFlight = true;
      try {
        const data = await this.request("/api/groups", {
          method: "POST",
          body: JSON.stringify(this.buildGroupsPayload()),
        });

        if (token !== this.pendingSaveToken) {
          return;
        }

        this.hydrateAccounts(data);
        if (data.listener_status) {
          this.applyStatus(data.listener_status, { preserveHint: true });
        } else {
          await this.refreshStatus({ preserveHint: true });
        }
        this.hintText = message;
      } catch (error) {
        if (token === this.pendingSaveToken) {
          this.hintText = error.message;
          await this.refreshStatus({ preserveHint: true });
        }
      } finally {
        if (token === this.pendingSaveToken) {
          this.saveInFlight = false;
        }
      }
    },

    getTagClass(tag) {
      const classes = {
        mother: "bg-yellow-100 text-yellow-700",
        child: "bg-blue-100 text-blue-700",
        unmarked: "bg-slate-100 text-slate-600",
        banned: "bg-red-100 text-red-700",
      };
      return classes[tag] || classes.unmarked;
    },

    getTagLabel(tag) {
      const labels = {
        mother: "母号",
        child: "子号",
        unmarked: "未标记",
        banned: "封号",
      };
      return labels[tag] || labels.unmarked;
    },

    getStateLabel(state) {
      const labels = {
        idle: "空闲",
        listening: "监听中",
        stopped: "已停止",
        error: "异常",
      };
      return labels[state] || state;
    },
  };
}
