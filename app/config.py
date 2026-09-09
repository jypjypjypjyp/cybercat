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
