from pathlib import Path
import os

# 配置目录：~/.config/cybercatoss
CONFIG_DIR = Path(os.environ.get("CYBERCATOSS_CONFIG_DIR", str(Path("~/.config/cybercatoss").expanduser())))
BOOKMARKS_FILE = CONFIG_DIR / "bookmarks.json"
# 会话空闲回收秒数
IDLE_TIMEOUT = float(os.environ.get("CYBERCATOSS_IDLE_TIMEOUT", 600))
# 钥匙串 service 名
KEYCHAIN_SERVICE = "cybercatoss"
# SFTP 下载预取（流水线）：默认开启。
# 实测（2026-09-10）：串行读是每 MB 32 次往返、在途请求仅 1 个，吞吐 ≈ 32KB/RTT；
# 开启预取后在途 8 个，实测提速 3.7x(RTT 5ms) ~ 5.5x(RTT 50ms)。
# 仅在纯本地回环（RTT≈0）时略慢约 14%（102→89 MB/s），真实网络均为净收益。
# 如需关闭：CYBERCAT_PREFETCH=0
PREFETCH = os.environ.get("CYBERCAT_PREFETCH", "1") not in ("0", "false", "no", "off")
# 预取并发数（越大吞吐越高，但会给服务器更多并发压力）
PREFETCH_CONCURRENCY = int(os.environ.get("CYBERCAT_PREFETCH_CONCURRENCY", "8"))
# 预览大小上限：超过就不预览（一个字节都不读），提示下载后查看
PREVIEW_MAX_BYTES = 512 * 1024 * 1024
# 顶栏链接状态的「活跃窗口」秒数：会话在这个时间内碰过显示「已连接」，超过则显示「空闲」
ACTIVE_WINDOW_SECONDS = 60.0
