"""解析 ~/.ssh/config，提取实体 Host 段（转为应用书签）。"""
import os
import re
from pathlib import Path


class SshHost:
    def __init__(self, alias, hostname=None, port=22, user=None, identity_file=None):
        self.alias = alias            # Host 别名
        self.hostname = hostname      # HostName 真实主机
        self.port = port              # Port
        self.user = user              # User
        self.identity_file = identity_file  # IdentityFile

    def to_bookmark(self):
        return {
            "name": self.alias,
            "host": self.hostname,
            "port": self.port,
            "username": self.user or "",
            "auth_method": "key" if self.identity_file else "password",
            "key_path": self.identity_file,
            "default_path": "/",
        }


def _is_wildcard(alias):
    """通配符别名（Host *、Host *.example.com 等）不是实体书签，跳过。"""
    return "*" in alias or "?" in alias


def parse_ssh_config(text):
    """解析 ssh config 文本，返回 SshHost 列表。跳过通配段、缺 HostName 的段。"""
    hosts = []
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 去掉行尾注释
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line:
            continue
        m = re.match(r"Host\s+(.+)$", line)
        if m:
            # 一个 Host 段可能含多个别名（Host a b c），取第一个作为书签名
            alias = m.group(1).strip().split()[0]
            current = SshHost(alias)
            hosts.append(current)
            continue
        if current is None:
            continue
        # 缩进属性
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0].lower(), parts[1].strip()
        if key == "hostname":
            current.hostname = value
        elif key == "port":
            try:
                current.port = int(value)
            except ValueError:
                pass
        elif key == "user":
            current.user = value
        elif key == "identityfile":
            current.identity_file = value
    # 过滤通配段与无真实 HostName 的段
    return [h for h in hosts if not _is_wildcard(h.alias) and h.hostname]


def load_ssh_config(path=None):
    """读取 ~/.ssh/config（默认）。返回 SshHost 列表。"""
    p = Path(path or os.path.expanduser("~/.ssh/config"))
    if not p.exists():
        return []
    return parse_ssh_config(p.read_text(encoding="utf-8"))
