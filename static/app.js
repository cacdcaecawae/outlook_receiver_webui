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
    hintText: "等待开始监听",

    stats: {
      total: 0,
      ready: 0,
      groups: 0,
    },

    tagOrder: ["mother", "child", "unmarked", "banned"],

    async init() {
      await this.loadAccounts();
      await this.refreshStatus();
      this.startPolling();
    },

    get currentGroup() {
      return this.groups.find((group) => group.id === this.selectedGroupId) || null;
    },

    get selectedAccount() {
      return this.accounts.find((account) => account.id === this.selectedAccountId) || null;
    },

    get canStart() {
      const account = this.selectedAccount;
      return !!(
        account &&
        account.ready &&
        account.tag !== "banned" &&
        !(this.state === "listening" && this.activeAccountId === this.selectedAccountId)
      );
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

    async loadAccounts() {
      try {
        const data = await this.request("/api/accounts");
        this.groups = data.account_groups || [];
        this.accounts = this.groups.flatMap((group) => group.accounts);
        this.accountsFile = data.accounts_file || "";
        this.groupsFile = data.groups_file || "";
        this.updateStats();
        this.ensureSelection();
      } catch (error) {
        this.hintText = error.message;
      }
    },

    async refreshStatus() {
      try {
        const data = await this.request("/api/status");
        this.state = data.is_listening ? "listening" : data.state || "idle";
        this.latestCode = data.latest_code || "";
        this.activeAccountId =
          typeof data.active_account_id === "number"
            ? data.active_account_id
            : typeof data.selected_index === "number"
              ? data.selected_index
              : null;

        if (data.error) {
          this.hintText = data.error;
        } else if (this.state === "listening") {
          this.hintText = this.latestCode ? "已收到验证码，继续监听中" : "正在监听最新邮件";
        } else if (this.state === "stopped") {
          this.hintText = "监听已停止，可切换账号继续";
        } else {
          this.hintText = "等待开始监听";
        }
      } catch (error) {
        this.hintText = error.message;
      }
    },

    startPolling() {
      setInterval(() => {
        this.refreshStatus();
      }, 1500);
    },

    updateStats() {
      this.stats.total = this.accounts.length;
      this.stats.ready = this.accounts.filter((account) => account.listenable ?? (account.ready && account.tag !== "banned")).length;
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
        this.state = data.is_listening ? "listening" : data.state || "idle";
        this.activeAccountId =
          typeof data.active_account_id === "number"
            ? data.active_account_id
            : typeof data.selected_index === "number"
              ? data.selected_index
              : null;
        this.hintText = `开始监听 ${this.selectedAccount?.email || ""}`.trim();
        await this.refreshStatus();
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
        this.state = data.is_listening ? "listening" : data.state || "stopped";
        this.activeAccountId =
          typeof data.active_account_id === "number"
            ? data.active_account_id
            : typeof data.selected_index === "number"
              ? data.selected_index
              : null;
        this.hintText = "监听已停止";
        await this.refreshStatus();
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

    cycleTag(email) {
      const group = this.groups.find((entry) => entry.accounts.some((account) => account.email === email));
      const account = group?.accounts.find((entry) => entry.email === email);
      if (!account) {
        return;
      }

      const currentIndex = this.tagOrder.indexOf(account.tag || "unmarked");
      const nextTag = this.tagOrder[(currentIndex + 1) % this.tagOrder.length];

      if (nextTag === "mother") {
        group.accounts.forEach((entry) => {
          if (entry.email !== email && entry.tag === "mother") {
            entry.tag = "child";
          }
        });
      }

      account.tag = nextTag;
      this.saveGroups("标签已更新");
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
      this.saveGroups("备注已保存");
    },

    async saveGroups(message = "分组已保存") {
      try {
        const payload = {
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

        const data = await this.request("/api/groups", {
          method: "POST",
          body: JSON.stringify(payload),
        });

        this.groups = data.account_groups || [];
        this.accounts = this.groups.flatMap((group) => group.accounts);
        this.updateStats();
        this.ensureSelection();
        this.hintText = message;
        await this.refreshStatus();
      } catch (error) {
        this.hintText = error.message;
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
  };
}

