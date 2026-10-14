"""预览用的小工具：扩展名 → Content-Type、HTTP Range 头解析。

只做两件事，不碰 SFTP：方便单测，也让 main.py 里的端点保持短。
"""
import posixpath

# 扩展名 → MIME。只列浏览器能直接渲染的类型，其余一律 application/octet-stream。
MIME_BY_EXT = {
    # 图片
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "bmp": "image/bmp",
    "ico": "image/x-icon",
    "svg": "image/svg+xml",
    # 视频
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "webm": "video/webm",
    "ogv": "video/ogg",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
    # 音频
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    # 文档
    "pdf": "application/pdf",
}

# 文本类：前端用 <pre> 渲染，这里统一给 text/plain；json 单独给便于阅读
TEXT_MIME = "text/plain; charset=utf-8"
TEXT_EXT = {
    "txt", "text", "log", "md", "markdown", "rst", "csv", "tsv", "ini", "conf", "cfg", "env",
    "yaml", "yml", "toml", "xml", "html", "htm", "css", "scss", "less",
    "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte",
    "py", "rb", "php", "go", "rs", "java", "kt", "swift", "c", "h", "cc", "cpp", "hpp",
    "cs", "m", "mm", "scala", "pl", "lua", "r", "sql", "sh", "bash", "zsh", "fish",
    "ps1", "bat", "dockerfile", "makefile", "gitignore", "editorconfig", "properties",
}


def guess_mime(path):
    """按扩展名猜 MIME；没有扩展名或没收录的返回 application/octet-stream。

    无扩展名的常见文本文件（Makefile / Dockerfile / .gitignore 之类）按文件名兜底。
    """
    name = posixpath.basename(path).lower()
    ext = posixpath.splitext(name)[1].lstrip(".")
    if ext == "json":
        return "application/json"
    if ext in MIME_BY_EXT:
        return MIME_BY_EXT[ext]
    if ext in TEXT_EXT:
        return TEXT_MIME
    if name in TEXT_EXT or name.lstrip(".") in TEXT_EXT:  # Makefile、Dockerfile、.gitignore
        return TEXT_MIME
    return "application/octet-stream"


def parse_range(header, size):
    """解析单个 bytes 范围。

    返回 None 表示没有 Range（按整文件返回），否则返回 (start, end)，两端都含。
    语法非法或范围无法满足时抛 ValueError，由调用方转成 416。
    只处理单段：视频播放器只会发单段，多段（bytes=a-b,c-d）按第一段处理。
    """
    if not header:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None  # 不认识的单位：按 RFC 7233 可以忽略，返回整文件
    spec = header[6:].split(",")[0].strip()
    if "-" not in spec:
        raise ValueError(f"bad range: {header}")
    first, _, last = spec.partition("-")
    try:
        if not first:  # bytes=-500：最后 500 字节
            n = int(last)
            if n <= 0:
                raise ValueError("bad suffix range")
            start, end = max(0, size - n), size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
    except ValueError:
        raise ValueError(f"bad range: {header}")
    if size == 0 or start > end or start >= size:
        raise ValueError(f"unsatisfiable range: {header}")
    return start, min(end, size - 1)
