# Outlook Receiver Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone local Web UI that loads Outlook accounts from a file, lets the user select one account, starts and stops a single listener, and shows the latest verification code.

**Architecture:** A small Python standard-library HTTP server serves one HTML page and JSON endpoints. A background listener thread owns the selected Outlook session and reports status back to the browser through a shared in-memory state object.

**Tech Stack:** Python 3 standard library, IMAP XOAUTH2, plain HTML/CSS/JavaScript, unittest

---

### Task 1: Project Skeleton And Account Loading

**Files:**
- Create: `E:\注册机codex\outlook_receiver_webui\app.py`
- Create: `E:\注册机codex\outlook_receiver_webui\receiver_core.py`
- Create: `E:\注册机codex\outlook_receiver_webui\tests\test_receiver_core.py`

- [ ] **Step 1: Write the failing test**

```python
def test_load_accounts_reads_dash_delimited_rows():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_receiver_core`
Expected: FAIL because helpers do not exist yet

- [ ] **Step 3: Write minimal implementation**

Create account-loading helpers and shared state containers.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_receiver_core`
Expected: PASS

### Task 2: Listener Logic

**Files:**
- Modify: `E:\注册机codex\outlook_receiver_webui\receiver_core.py`
- Modify: `E:\注册机codex\outlook_receiver_webui\tests\test_receiver_core.py`

- [ ] **Step 1: Write the failing test**

```python
def test_listener_state_updates_when_message_arrives():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_receiver_core`
Expected: FAIL because listener flow is incomplete

- [ ] **Step 3: Write minimal implementation**

Implement start/stop control, in-memory status, and message extraction helpers.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_receiver_core`
Expected: PASS

### Task 3: Web UI

**Files:**
- Modify: `E:\注册机codex\outlook_receiver_webui\app.py`
- Create: `E:\注册机codex\outlook_receiver_webui\static\index.html`
- Create: `E:\注册机codex\outlook_receiver_webui\static\app.js`
- Create: `E:\注册机codex\outlook_receiver_webui\static\styles.css`

- [ ] **Step 1: Write the failing test**

```python
def test_http_handler_returns_account_list_json():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_receiver_core`
Expected: FAIL because HTTP endpoints do not exist yet

- [ ] **Step 3: Write minimal implementation**

Serve the page and implement `/api/accounts`, `/api/start`, `/api/stop`, and `/api/status`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_receiver_core`
Expected: PASS

### Task 4: Verification

**Files:**
- Modify: `E:\注册机codex\outlook_receiver_webui\README.md`

- [ ] **Step 1: Add usage instructions**

Document how to launch the standalone UI and point it at `outlook_accounts.txt`.

- [ ] **Step 2: Run verification**

Run: `python -m unittest`
Expected: PASS

- [ ] **Step 3: Run syntax checks**

Run: `python -m py_compile app.py receiver_core.py`
Expected: PASS
