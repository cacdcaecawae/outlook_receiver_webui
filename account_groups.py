from __future__ import annotations

from pathlib import Path
import json
from typing import Any


ACCOUNT_GROUP_VERSION = 1
DEFAULT_GROUP_SIZE = 5
DEFAULT_MOTHER_TAG = "mother"
DEFAULT_CHILD_TAG = "child"
DEFAULT_UNMARKED_TAG = "unmarked"
DEFAULT_BANNED_TAG = "banned"
UNASSIGNED_GROUP_ID = "group-unassigned"
UNASSIGNED_GROUP_NAME = "\u672a\u5206\u7ec4"
VALID_TAGS = {
    DEFAULT_MOTHER_TAG,
    DEFAULT_CHILD_TAG,
    DEFAULT_UNMARKED_TAG,
    DEFAULT_BANNED_TAG,
}


def resolve_groups_file(accounts_file: Path, cli_path: str = "") -> Path:
    if cli_path:
        return Path(cli_path)
    return accounts_file.with_name("account_groups.json")


def build_default_group_config(
    accounts: list[dict[str, Any]],
    group_size: int = DEFAULT_GROUP_SIZE,
) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for offset in range(0, len(accounts), group_size):
        group_index = (offset // group_size) + 1
        group_accounts: list[dict[str, Any]] = []
        for position, account in enumerate(accounts[offset : offset + group_size]):
            _ = position
            group_accounts.append({"email": account["email"], "tag": DEFAULT_UNMARKED_TAG, "note": ""})
        groups.append(
            {
                "id": f"group-{group_index}",
                "name": f"\u7b2c {group_index} \u7ec4",
                "accounts": group_accounts,
            }
        )
    return {"version": ACCOUNT_GROUP_VERSION, "groups": groups}


def load_group_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": ACCOUNT_GROUP_VERSION, "groups": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_group_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_group_config(
    path: Path,
    accounts: list[dict[str, Any]],
    group_size: int = DEFAULT_GROUP_SIZE,
) -> dict[str, Any]:
    if path.is_file():
        config = normalize_loaded_group_config(load_group_config(path), accounts, group_size=group_size)
        write_group_config(path, config)
        return config
    config = build_default_group_config(accounts, group_size=group_size)
    write_group_config(path, config)
    return config


def normalize_loaded_group_config(
    raw_config: dict[str, Any],
    accounts: list[dict[str, Any]],
    group_size: int = DEFAULT_GROUP_SIZE,
) -> dict[str, Any]:
    raw_config = migrate_legacy_default_tags(raw_config)
    account_by_email = {account["email"]: account for account in accounts}
    ordered_emails = [account["email"] for account in accounts]
    consumed: set[str] = set()
    groups: list[dict[str, Any]] = []

    for group_index, raw_group in enumerate(raw_config.get("groups", []), start=1):
        group_id = str(raw_group.get("id") or f"group-{group_index}")
        group_name = (
            str(raw_group.get("name") or f"\u7b2c {group_index} \u7ec4").strip()
            or f"\u7b2c {group_index} \u7ec4"
        )
        group_accounts: list[dict[str, Any]] = []
        local_seen: set[str] = set()

        for raw_account in raw_group.get("accounts", []):
            email = str(raw_account.get("email") or "").strip()
            if not email or email not in account_by_email or email in consumed or email in local_seen:
                continue
            local_seen.add(email)
            consumed.add(email)
            group_accounts.append(
                {
                    "email": email,
                    "tag": normalize_tag(raw_account.get("tag")),
                    "note": normalize_note(raw_account.get("note")),
                }
            )

        if group_accounts or not raw_group.get("accounts"):
            groups.append({"id": group_id, "name": group_name, "accounts": group_accounts})

    remaining = [email for email in ordered_emails if email not in consumed]
    next_index = len(groups) + 1
    for offset in range(0, len(remaining), group_size):
        chunk = remaining[offset : offset + group_size]
        group_accounts = [
            {
                "email": email,
                "tag": DEFAULT_UNMARKED_TAG,
                "note": "",
            }
            for position, email in enumerate(chunk)
        ]
        groups.append(
            {
                "id": f"group-{next_index}",
                "name": f"\u7b2c {next_index} \u7ec4",
                "accounts": group_accounts,
            }
        )
        next_index += 1

    return {"version": ACCOUNT_GROUP_VERSION, "groups": groups}


def normalize_submitted_group_config(
    raw_config: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    account_by_email = {account["email"]: account for account in accounts}
    ordered_emails = [account["email"] for account in accounts]
    consumed: set[str] = set()
    groups: list[dict[str, Any]] = []

    for group_index, raw_group in enumerate(raw_config.get("groups", []), start=1):
        group_id = str(raw_group.get("id") or f"group-{group_index}")
        group_name = (
            str(raw_group.get("name") or f"\u5206\u7ec4 {group_index}").strip()
            or f"\u5206\u7ec4 {group_index}"
        )
        group_accounts: list[dict[str, Any]] = []
        local_seen: set[str] = set()

        for raw_account in raw_group.get("accounts", []):
            email = str(raw_account.get("email") or "").strip()
            if not email or email not in account_by_email or email in consumed or email in local_seen:
                continue
            local_seen.add(email)
            consumed.add(email)
            group_accounts.append(
                {
                    "email": email,
                    "tag": normalize_tag(raw_account.get("tag")),
                    "note": normalize_note(raw_account.get("note")),
                }
            )

        groups.append({"id": group_id, "name": group_name, "accounts": group_accounts})

    remaining = [email for email in ordered_emails if email not in consumed]
    if remaining:
        groups.append(
            {
                "id": UNASSIGNED_GROUP_ID,
                "name": UNASSIGNED_GROUP_NAME,
                "accounts": [{"email": email, "tag": DEFAULT_UNMARKED_TAG, "note": ""} for email in remaining],
            }
        )

    return {"version": ACCOUNT_GROUP_VERSION, "groups": groups}


def materialize_account_groups(
    config: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    account_by_email = {account["email"]: account for account in accounts}
    groups: list[dict[str, Any]] = []
    ordered_accounts: list[dict[str, Any]] = []

    for group_index, group in enumerate(config.get("groups", []), start=1):
        group_accounts: list[dict[str, Any]] = []
        for entry in group.get("accounts", []):
            account = account_by_email.get(entry["email"])
            if account is None:
                continue
            hydrated = dict(account)
            hydrated["tag"] = normalize_tag(entry.get("tag"))
            hydrated["note"] = normalize_note(entry.get("note"))
            hydrated["group_id"] = group["id"]
            hydrated["group_name"] = group["name"]
            hydrated["listenable"] = hydrated["ready"] and hydrated["tag"] != DEFAULT_BANNED_TAG
            hydrated["disabled_reason"] = get_disabled_reason(hydrated)
            group_accounts.append(hydrated)
            ordered_accounts.append(hydrated)

        groups.append(
            {
                "id": group["id"],
                "name": group["name"],
                "label": group["name"],
                "group_index": group_index,
                "count": len(group_accounts),
                "accounts": group_accounts,
            }
        )

    return groups, ordered_accounts


def normalize_tag(value: Any) -> str | None:
    if value in VALID_TAGS:
        return str(value)
    return DEFAULT_UNMARKED_TAG


def normalize_note(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def migrate_legacy_default_tags(raw_config: dict[str, Any]) -> dict[str, Any]:
    groups = raw_config.get("groups", [])
    if not groups:
        return raw_config

    should_migrate = True
    for index, group in enumerate(groups, start=1):
        if str(group.get("name") or "") != f"\u7b2c {index} \u7ec4":
            should_migrate = False
            break
        for account in group.get("accounts", []):
            if normalize_note(account.get("note")):
                should_migrate = False
                break
            if account.get("tag") not in {DEFAULT_MOTHER_TAG, DEFAULT_CHILD_TAG, None}:
                should_migrate = False
                break
        if not should_migrate:
            break

    if not should_migrate:
        return raw_config

    migrated_groups: list[dict[str, Any]] = []
    for group in groups:
        migrated_groups.append(
            {
                "id": group.get("id"),
                "name": group.get("name"),
                "accounts": [
                    {
                        "email": account.get("email"),
                        "tag": DEFAULT_UNMARKED_TAG,
                        "note": "",
                    }
                    for account in group.get("accounts", [])
                ],
            }
        )

    return {"version": ACCOUNT_GROUP_VERSION, "groups": migrated_groups}

def get_disabled_reason(account: dict[str, Any]) -> str | None:
    if not account.get("ready"):
        return "missing_credentials"
    if account.get("tag") == DEFAULT_BANNED_TAG:
        return "banned"
    return None
