"""从 ~/.ssh/config 同步到书签：存在的覆盖，没有的新建。"""
import time
import uuid

import app.bookmarks as bookmarks
import app.ssh_config as sshcfg


def sync_from_ssh_config(path=None):
    """同步 ~/.ssh/config 到书签。

    以 Host 别名为匹配键（对应书签的 name）：
      - 书签里 name == 别名 → 覆盖 host/port/username/auth_method/key_path（保留 id 与钥匙串密码）
      - 没有 → 新建书签
    同步不改动钥匙串里的密码。

    返回 {created, updated, skipped}。
    """
    hosts = sshcfg.load_ssh_config(path)
    existing = bookmarks.load_bookmarks()
    name_to_bm = {bm["name"]: bm for bm in existing}

    created = updated = skipped = 0
    for h in hosts:
        alias = h.alias
        fields = {
            "name": alias,
            "host": h.hostname,
            "port": h.port,
            "username": h.user or "",
            "auth_method": "key" if h.identity_file else "password",
            "key_path": h.identity_file,
        }
        if alias in name_to_bm:
            existing_bm = name_to_bm[alias]
            for k in ("host", "port", "username", "auth_method", "key_path"):
                existing_bm[k] = fields[k]
            existing_bm["timestamp"] = int(time.time())
            updated += 1
        else:
            bm = dict(fields)
            bm["id"] = uuid.uuid4().hex[:8]
            bm["default_path"] = "/"
            bm["timestamp"] = int(time.time())
            existing.append(bm)
            name_to_bm[alias] = bm
            created += 1

    bookmarks.save_bookmarks(existing)
    return {"created": created, "updated": updated, "skipped": skipped}
