"""运行时可改的参数：存 ~/.config/cybercatoss/settings.json，覆盖 config 里的默认值。

界面只认这里的 FIELDS；值按展示单位存（MB / 分钟），应用时换算成 config 的原始单位。
"""
import json
import threading

import app.config as config

FILE = config.CONFIG_DIR / "settings.json"

# 可调项。type: int / bool / choice；key 同时是 JSON 字段名
FIELDS = [
    {"key": "preview_lanes", "label": "预览并行通道", "type": "int", "min": 1, "max": 8,
     "unit": "条", "help": "预览视频时同时挂几条读通道；越大越快，也更压服务器"},
    {"key": "preview_max_mb", "label": "预览大小上限", "type": "int", "min": 16, "max": 4096,
     "unit": "MB", "help": "超过这个大小就不预览，只提示下载"},
    {"key": "idle_timeout_min", "label": "空闲回收", "type": "int", "min": 1, "max": 1440,
     "unit": "分钟", "help": "会话闲置这么久后断开"},
    {"key": "prefetch", "label": "下载预取加速", "type": "bool",
     "help": "让多个读请求同时在路上，跨网络下载通常快 3–5 倍"},
    {"key": "prefetch_concurrency", "label": "预取并发数", "type": "int", "min": 1, "max": 16,
     "unit": "个", "help": "越大越快，也更压服务器"},
    {"key": "transcode_height", "label": "转码高度上限", "type": "choice",
     "choices": [480, 720, 1080], "unit": "p", "help": "实时转码输出的最大高度"},
    {"key": "transcode_crf", "label": "转码画质", "type": "int", "min": 18, "max": 35,
     "help": "越大越糊、越小越清楚（文件也越大）"},
    {"key": "transcode_preset", "label": "转码速度档", "type": "choice",
     "choices": ["ultrafast", "veryfast", "faster", "medium"],
     "help": "越快越省 CPU，画质略降"},
    {"key": "transcode_default", "label": "默认转码播放", "type": "bool",
     "help": "打开视频直接走转码流（码率低、不卡），代价是不能拖动进度、吃服务器 CPU"},
]

_ATTR = {
    "preview_lanes": "PREVIEW_LANES",
    "preview_max_mb": "PREVIEW_MAX_BYTES",
    "idle_timeout_min": "IDLE_TIMEOUT",
    "prefetch": "PREFETCH",
    "prefetch_concurrency": "PREFETCH_CONCURRENCY",
    "transcode_height": "TRANSCODE_HEIGHT",
    "transcode_crf": "TRANSCODE_CRF",
    "transcode_preset": "TRANSCODE_PRESET",
    "transcode_default": "TRANSCODE_DEFAULT",
}

_lock = threading.Lock()


def _to_config(key, value):
    if key == "preview_max_mb":
        return value * 1024 * 1024
    if key == "idle_timeout_min":
        return value * 60
    return value


def _from_config(key):
    value = getattr(config, _ATTR[key])
    if key == "preview_max_mb":
        return value // (1024 * 1024)
    if key == "idle_timeout_min":
        return int(round(value / 60))
    return value


def values():
    """当前生效的值（展示单位）。"""
    return {field["key"]: _from_config(field["key"]) for field in FIELDS}


def _check(field, value):
    kind = field["type"]
    if kind == "bool":
        return bool(value)
    if kind == "choice":
        if value not in field["choices"]:
            raise ValueError(f"{field['label']}只能是 {' / '.join(map(str, field['choices']))}")
        return value
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field['label']}要填整数")
    if not field["min"] <= number <= field["max"]:
        raise ValueError(f"{field['label']}要在 {field['min']}–{field['max']} 之间")
    return number


def _apply(settings):
    for key, value in settings.items():
        setattr(config, _ATTR[key], _to_config(key, value))


def update(patch):
    """校验、写盘、立即生效；有不合法项就抛 ValueError 且什么都不改。"""
    with _lock:
        current = values()
        for field in FIELDS:
            if field["key"] in patch:
                current[field["key"]] = _check(field, patch[field["key"]])
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_name(FILE.name + ".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2))
        tmp.replace(FILE)
        _apply(current)
        return current


def load():
    """启动时套用已保存的值；文件缺失或坏了就按 config 的默认值跑。"""
    try:
        raw = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    patch = {key: raw[key] for key in _ATTR if key in raw}
    try:
        update(patch)
    except (ValueError, OSError):
        pass
