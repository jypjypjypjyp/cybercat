from pathlib import Path
import os

# 配置目录：~/.config/cybercatoss
CONFIG_DIR = Path(os.environ.get("CYBERCATOSS_CONFIG_DIR", str(Path("~/.config/cybercatoss").expanduser())))
BOOKMARKS_FILE = CONFIG_DIR / "bookmarks.json"
# 会话空闲回收秒数
IDLE_TIMEOUT = float(os.environ.get("CYBERCATOSS_IDLE_TIMEOUT", 600))
# 钥匙串 service 名
KEYCHAIN_SERVICE = "cybercatoss"
# 列表默认分页大小
DEFAULT_PAGE_SIZE = 100
# SFTP 下载预取：默认关（本地/局域网串行更快，且避免对远程服务器并发放大负载）。
# 高延迟公网场景可设 CYBERCAT_PREFETCH=1 显式开启。
PREFETCH = os.environ.get("CYBERCAT_PREFETCH", "0") in ("1", "true", "yes", "on")
# 预取并发数
PREFETCH_CONCURRENCY = int(os.environ.get("CYBERCAT_PREFETCH_CONCURRENCY", "8"))
