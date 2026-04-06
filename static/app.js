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
    accountsRoot: "",
    accountsFiles: [],
    groupsFile: "",
    customTags: [],
    searchQuery: "",
    searchFocused: false,
    draggedEmail: "",
    dragOverGroupId: "",
    tagMenuEmail: "",
    webUsageMenuEmail: "",

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
    webUsageOptions: [
      { value: "unknown", label: "GPT未知" },
      { value: "free", label: "GPT空闲" },
      { value: "busy", label: "GPT占用" },
    ],

    async init() {
      this.baseTitle = document.title;
      await this.loadAccounts();
      await this.refreshStatus();
      this.startEventStream();
    },

    get normalizedSearch() {
      return this.searchQuery.trim().toLowerCase();
    },

    get visibleGroups() {
      return this.groups;
    },

    get searchResults() {
      const query = this.normalizedSearch;
      if (!query) {
        return [];
      }

      return this.accounts
        .filter((account) => this.accountMatchesSearch(account))
        .slice(0, 8);
    },

    get showSearchResults() {
      return this.searchFocused && this.searchResults.length > 0;
    },

    get showEmptySearchResults() {
      return this.searchFocused && !!this.normalizedSearch && this.searchResults.length === 0;
    },

    get currentGroup() {
      const visibleGroups = this.visibleGroups;
      if (!visibleGroups.length) {
        return null;
      }
      return visibleGroups.find((group) => group.id === this.selectedGroupId) || visibleGroups[0];
    },

    get displayedAccounts() {
      return this.currentGroup?.accounts || [];
    },

    get availableTagOptions() {
      return [
        ...this.tagOptions,
        ...this.customTags.map((tag) => ({ value: tag, label: tag })),
      ];
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
        connecting: "连接中",
        connected: "推送",
        reconnecting: "重连中",
        fallback: "轮询",
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

    accountMatchesSearch(account) {
      const query = this.normalizedSearch;
      if (!query) {
        return true;
      }

      return [
        account.email,
        account.note,
        account.group_name,
        this.getTagLabel(account.tag),
        this.getWebUsageLabel(account.web_usage),
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query));
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
      this.accountsRoot = data.accounts_root || "";
      this.accountsFiles = data.accounts_files || [];
      this.groupsFile = data.groups_file || "";
      this.customTags = data.custom_tags || [];
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

    async reloadAccounts() {
      try {
        const data = await this.request("/api/reload-accounts", {
          method: "POST",
          body: JSON.stringify({}),
        });
        this.hydrateAccounts(data);
        if (data.listener_status) {
          this.applyStatus(data.listener_status, { preserveHint: true });
        } else {
          await this.refreshStatus({ preserveHint: true });
        }
        this.hintText = `已刷新 ${this.accounts.length} 个账号，来源 ${this.accountsFiles.length} 个文件`;
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

    handleSearchInput() {
      this.searchFocused = true;
    },

    clearSearch() {
      this.searchQuery = "";
      this.searchFocused = false;
    },

    selectSearchResult(account) {
      this.searchQuery = "";
      this.searchFocused = false;
      this.selectedGroupId = account.group_id;
      this.selectedAccountId = account.id;
      this.passwordVisible = false;
      this.scrollAccountIntoView(account.id);
    },

    scrollAccountIntoView(accountId) {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          const target = document.querySelector(`[data-account-id="${accountId}"]`);
          target?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
      });
    },

    ensureSelection() {
      const visibleGroups = this.visibleGroups;
      if (!visibleGroups.length) {
        this.selectedGroupId = "";
        this.selectedAccountId = null;
        return;
      }

      if (!this.selectedGroupId || !visibleGroups.find((group) => group.id === this.selectedGroupId)) {
        this.selectedGroupId = visibleGroups[0].id;
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
      this.tagMenuEmail = "";
      this.syncAccountFlags(account);
      this.updateStats();
      const shouldStopActiveListener = tag === "banned" && this.state === "listening" && this.activeAccountId === account.id;
      this.queueGroupsSave(
        shouldStopActiveListener ? "封号已标记，正在停止监听..." : "标签已更新",
        { immediate: shouldStopActiveListener },
      );
    },

    createCustomTag(email) {
      const rawValue = window.prompt("请输入新的共用标记", "");
      if (rawValue === null) {
        return;
      }

      const nextTag = rawValue.trim();
      if (!nextTag) {
        return;
      }

      const builtinTag = this.tagOptions.find(
        (option) => option.label === nextTag || option.value === nextTag,
      );
      if (builtinTag) {
        this.setTag(email, builtinTag.value);
        return;
      }

      if (!this.customTags.includes(nextTag)) {
        this.customTags.push(nextTag);
      }
      this.setTag(email, nextTag);
    },

    toggleTagMenu(email) {
      this.webUsageMenuEmail = "";
      this.tagMenuEmail = this.tagMenuEmail === email ? "" : email;
    },

    isTagMenuOpen(email) {
      return this.tagMenuEmail === email;
    },

    toggleWebUsageMenu(email) {
      this.tagMenuEmail = "";
      this.webUsageMenuEmail = this.webUsageMenuEmail === email ? "" : email;
    },

    isWebUsageMenuOpen(email) {
      return this.webUsageMenuEmail === email;
    },

    setWebUsage(email, webUsage) {
      const group = this.groups.find((entry) => entry.accounts.some((account) => account.email === email));
      const account = group?.accounts.find((entry) => entry.email === email);
      if (!account || account.web_usage === webUsage) {
        this.webUsageMenuEmail = "";
        return;
      }

      account.web_usage = webUsage;
      this.webUsageMenuEmail = "";
      this.queueGroupsSave("网页版状态已更新");
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

    createGroup() {
      const name = (window.prompt("请输入新分组名称", "") || "").trim();
      if (!name) {
        return;
      }

      const groupId = this.buildGroupId(name);
      this.groups.push({
        id: groupId,
        name,
        label: name,
        group_index: this.groups.length + 1,
        count: 0,
        accounts: [],
      });
      this.searchQuery = "";
      this.updateStats();
      this.selectGroup(groupId);
      this.queueGroupsSave(`已创建分组 ${name}`, { immediate: true });
    },

    renameGroup(groupId) {
      const group = this.groups.find((entry) => entry.id === groupId);
      if (!group) {
        return;
      }

      const name = (window.prompt("请输入新的分组名称", group.name || "") || "").trim();
      if (!name || name === group.name) {
        return;
      }

      group.name = name;
      group.label = name;
      group.accounts.forEach((account) => {
        account.group_name = name;
      });
      this.queueGroupsSave(`已重命名为 ${name}`, { immediate: true });
    },

    deleteGroup(groupId) {
      const groupIndex = this.groups.findIndex((entry) => entry.id === groupId);
      if (groupIndex < 0) {
        return;
      }

      const group = this.groups[groupIndex];
      const hasAccounts = group.accounts.length > 0;
      const confirmed = window.confirm(
        hasAccounts
          ? `删除 ${group.name} 后，组内账号会回到未分组。是否继续？`
          : `确认删除空分组 ${group.name}？`,
      );
      if (!confirmed) {
        return;
      }

      this.groups.splice(groupIndex, 1);
      if (hasAccounts) {
        const unassignedGroup = this.ensureUnassignedGroup();
        group.accounts.forEach((account) => {
          this.placeAccountIntoGroup(account, unassignedGroup);
        });
      }

      this.updateStats();
      this.ensureSelection();
      this.queueGroupsSave(`已删除分组 ${group.name}`, { immediate: true });
    },

    ensureUnassignedGroup() {
      let group = this.groups.find((entry) => entry.id === "group-unassigned");
      if (group) {
        return group;
      }

      group = {
        id: "group-unassigned",
        name: "未分组",
        label: "未分组",
        group_index: this.groups.length + 1,
        count: 0,
        accounts: [],
      };
      this.groups.push(group);
      return group;
    },

    buildGroupId(name) {
      const safePrefix = name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      const prefix = safePrefix || "group";
      let candidate = `custom-${prefix}`;
      let suffix = 1;
      while (this.groups.some((group) => group.id === candidate)) {
        suffix += 1;
        candidate = `custom-${prefix}-${suffix}`;
      }
      return candidate;
    },

    beginAccountDrag(email, event) {
      this.draggedEmail = email;
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", email);
      }
    },

    endAccountDrag() {
      this.draggedEmail = "";
      this.dragOverGroupId = "";
    },

    allowGroupDrop(groupId, event) {
      if (!this.draggedEmail && !event.dataTransfer) {
        return;
      }
      event.preventDefault();
      this.dragOverGroupId = groupId;
    },

    leaveGroupDrop(groupId) {
      if (this.dragOverGroupId === groupId) {
        this.dragOverGroupId = "";
      }
    },

    handleGroupDrop(groupId, event) {
      event.preventDefault();
      const email = this.draggedEmail || event.dataTransfer?.getData("text/plain") || "";
      this.dragOverGroupId = "";
      this.draggedEmail = "";
      if (email) {
        this.moveAccountToGroup(email, groupId);
      }
    },

    moveAccountToGroup(email, targetGroupId) {
      const sourceGroup = this.groups.find((group) => group.accounts.some((account) => account.email === email));
      const targetGroup = this.groups.find((group) => group.id === targetGroupId);
      const accountIndex = sourceGroup?.accounts.findIndex((account) => account.email === email) ?? -1;
      if (!sourceGroup || !targetGroup || accountIndex < 0 || sourceGroup.id === targetGroupId) {
        return;
      }

      const [account] = sourceGroup.accounts.splice(accountIndex, 1);
      this.placeAccountIntoGroup(account, targetGroup);
      this.updateStats();
      this.selectGroup(targetGroupId);
      this.selectedAccountId = account.id;
      this.scrollAccountIntoView(account.id);
      this.queueGroupsSave(`已移入 ${targetGroup.name}`, { immediate: true });
    },

    placeAccountIntoGroup(account, targetGroup) {
      if (account.tag === "mother") {
        targetGroup.accounts.forEach((entry) => {
          if (entry.tag === "mother") {
            entry.tag = "child";
            this.syncAccountFlags(entry);
          }
        });
      }

      account.group_id = targetGroup.id;
      account.group_name = targetGroup.name;
      targetGroup.accounts.push(account);
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
        custom_tags: this.customTags,
        groups: this.groups.map((group) => ({
          id: group.id,
          name: group.name,
          accounts: group.accounts.map((account) => ({
            email: account.email,
            tag: account.tag,
            web_usage: account.web_usage || "unknown",
            note: account.note || "",
          })),
        })),
      };
    },

    async persistGroups(token, message = "分组已保存") {
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
      }
    },

    getTagClass(tag) {
      const classes = {
        mother: "bg-yellow-100 text-yellow-700",
        child: "bg-blue-100 text-blue-700",
        unmarked: "bg-slate-100 text-slate-600",
        banned: "bg-red-100 text-red-700",
      };
      return classes[tag] || "bg-violet-100 text-violet-700";
    },

    getTagLabel(tag) {
      const labels = {
        mother: "母号",
        child: "子号",
        unmarked: "未标记",
        banned: "封号",
      };
      return labels[tag] || tag || labels.unmarked;
    },

    getWebUsageClass(webUsage) {
      const classes = {
        unknown: "bg-slate-100 text-slate-600",
        free: "bg-emerald-100 text-emerald-700",
        busy: "bg-rose-100 text-rose-700",
      };
      return classes[webUsage] || classes.unknown;
    },

    getWebUsageLabel(webUsage) {
      const labels = {
        unknown: "GPT未知",
        free: "GPT空闲",
        busy: "GPT占用",
      };
      return labels[webUsage] || labels.unknown;
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
