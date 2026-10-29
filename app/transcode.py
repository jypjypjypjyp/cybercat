"""实时转码：远端 ffmpeg 边转边播，不落盘。

链路：浏览器 ← HTTP ← 本机 ← SSH exec ← 远端 ffmpeg ← 远端源文件。
服务器没有 ffmpeg 时上传本机缓存的静态版，同一个会话内复用。
"""
import os
import posixpath
import shlex
import socket
import time
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import NamedTuple

import app.config as config

CHUNK = 64 * 1024
FIRST_BYTE_TIMEOUT = 45

CACHE_DIR = Path(os.environ.get("CYBERCAT_CACHE_DIR") or (Path.home() / ".cache" / "cybercatoss"))

# 静态版 ffmpeg（Linux 通用，不依赖服务器已有的库）。只收录服务器常见的两种架构。
BUILDS = {
    "linux-amd64": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
    "linux-arm64": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz",
}


class TranscodeError(Exception):
    """转码拉不起来：没有可用的 ffmpeg、读不到文件、ffmpeg 立刻失败。"""


def platform_key(sysname, machine):
    """uname 输出 → 可移植版的键；不认识的平台返回 None。"""
    sysname = (sysname or "").lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        (machine or "").lower()
    )
    if sysname.startswith("linux") and arch:
        return f"linux-{arch}"
    return None


def local_binary(key):
    """本机缓存的静态 ffmpeg；没有就下载官方静态构建解出来（只解 ffmpeg 本体）。"""
    if config.FFMPEG_PORTABLE:
        path = Path(config.FFMPEG_PORTABLE).expanduser()
        if path.exists():
            return path
        raise TranscodeError(f"CYBERCAT_FFMPEG_PORTABLE 指向的文件不存在：{path}")
    path = CACHE_DIR / "ffmpeg" / key / "ffmpeg"
    if path.exists():
        return path
    url = BUILDS.get(key)
    if not url:
        raise TranscodeError(f"没有 {key} 的可移植 ffmpeg，可用 CYBERCAT_FFMPEG_PORTABLE 指定")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ffmpeg.tar.xz"
        with urllib.request.urlopen(url, timeout=60) as resp, open(archive, "wb") as out:
            while chunk := resp.read(1024 * 1024):
                out.write(chunk)
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if posixpath.basename(member.name) == "ffmpeg" and member.isfile():
                    src = tf.extractfile(member)
                    path.write_bytes(src.read())
                    break
    path.chmod(0o755)
    return path


def _run(session, command, timeout=20):
    """在远端跑一条短命令，返回 (退出码, 输出)。

    有的跳板机丢弃退出码（恒为 0），所以判断成败一律看输出。
    """
    chan = session.ssh.get_transport().open_session(timeout=timeout)
    try:
        chan.settimeout(timeout)
        chan.exec_command(command)
        out = []
        while True:
            data = chan.recv(65536)
            if not data:
                break
            out.append(data)
        return chan.recv_exit_status(), b"".join(out)
    except socket.timeout:
        raise TranscodeError("远端命令超时")
    finally:
        try:
            chan.close()
        except Exception:
            pass


def _mkdir(client, remote):
    parent = posixpath.dirname(remote)
    cur = ""
    for part in parent.split("/"):
        if not part:
            continue
        cur += "/" + part
        try:
            client.stat(cur)
        except IOError:
            try:
                client.mkdir(cur)
            except IOError:
                pass


def _upload(session, local, remote):
    """把静态 ffmpeg 传到远端家目录（走传输通道，开启写流水线）。"""
    client = session.transfer_client()
    with session.transfer_gate:
        _mkdir(client, remote)
        with client.open(remote, "wb") as dst:
            dst.set_pipelined(True)
            with open(local, "rb") as src:
                while chunk := src.read(256 * 1024):
                    dst.write(chunk)
    # 权限位由调用方用 shell 设置（见 ensure_ffmpeg）


def _output(session, command, timeout=20):
    """跑一条命令，返回合并后的输出文本。"""
    _, out = _run(session, command, timeout)
    return out.decode("utf-8", "replace")


_PROBE = (
    'echo "SYS=$(command -v ffmpeg 2>/dev/null || true)"; '
    'echo "OS=$(uname -s) $(uname -m)"; '
    'for k in linux-amd64 linux-arm64; do '
    'P="$HOME/.cache/cybercatoss/ffmpeg-$k"; '
    'if [ -x "$P" ]; then echo "OK-$k=$(readlink -f "$P")"; '
    'echo "VER-$k=$("$P" -version 2>/dev/null | head -1)"; fi; done'
)


class Probe(NamedTuple):
    """一次探测的结果。"""
    sys_path: str          # 服务器自带 ffmpeg 的路径（没有为空）
    os_name: str
    arch: str
    portable: dict         # 平台键 → 便携版真实路径
    versions: dict         # 平台键 → -version 输出首行


def _probe(session):
    """一次通道问全：系统 ffmpeg、平台、便携版能不能跑。"""
    sys_path, os_name, arch, portable, versions = "", "", "", {}, {}
    for line in _output(session, _PROBE).splitlines():
        line = line.strip()
        if line.startswith("SYS="):
            sys_path = line[4:].strip()
        elif line.startswith("OS="):
            parts = line[3:].split()
            os_name = parts[0] if parts else ""
            arch = parts[1] if len(parts) > 1 else ""
        elif line.startswith("OK-"):
            name, _, path = line[3:].partition("=")
            portable[name] = path.strip()
        elif line.startswith("VER-"):
            name, _, ver = line[4:].partition("=")
            versions[name] = ver.strip()
    return Probe(sys_path, os_name, arch, portable, versions)


def ensure_ffmpeg(session, upload=True):
    """远端可用的 ffmpeg 绝对路径；没有就上传静态版。

    upload=False 只探测不传。
    """
    with session.ffmpeg_lock:
        if session.ffmpeg:
            return session.ffmpeg

        probe = _probe(session)
        if probe.sys_path:
            session.ffmpeg = probe.sys_path
            return probe.sys_path

        key = platform_key(probe.os_name, probe.arch)
        if not key:
            raise TranscodeError("服务器没有 ffmpeg，也没有适配它平台的便携版")
        if key in probe.portable and "ffmpeg version" in probe.versions.get(key, ""):
            session.ffmpeg = probe.portable[key]
            return session.ffmpeg
        if not upload:
            return None

        suffix = f"/.cache/cybercatoss/ffmpeg-{key}"
        expr = f'"$HOME"{shlex.quote(suffix)}'
        remote = session.client.normalize(".").rstrip("/") + suffix
        _upload(session, local_binary(key), remote)
        try:
            info = session.transfer_client().stat(remote)
        except Exception as exc:
            raise TranscodeError(f"ffmpeg 上传后远端找不到文件：{exc}")
        if info.st_size < 1024 * 1024:
            raise TranscodeError(f"ffmpeg 上传不完整（只有 {info.st_size} 字节）")
        # SFTP 写出来是 644，用 shell 补执行位并同时验证能跑
        ver = _output(session, f"chmod 755 {expr}; {expr} -version 2>&1 | head -1").strip()
        if "ffmpeg version" not in ver:
            raise TranscodeError(f"便携版 ffmpeg 传上去了，但远端跑不起来：{ver or '没有输出'}")
        resolved = _output(session, f"readlink -f {expr}").strip().splitlines()
        session.ffmpeg = resolved[-1] if resolved else expr
        return session.ffmpeg


def warm(session):
    """后台探测一次远端 ffmpeg，结果记到会话上。"""
    if session.ffmpeg or getattr(session, "_ffmpeg_warm", False):
        return
    session._ffmpeg_warm = True

    def run():
        try:
            ensure_ffmpeg(session, upload=False)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True, name="ffmpeg-warm").start()


def build_command(ffmpeg, path, start=0.0, height=None, crf=None, preset=None, path_expr=None):
    """拼远端 ffmpeg 命令：降到预览尺寸、输出分片 MP4 到标准输出。

    path_expr：已处理好的 shell 表达式（如 "$P"），给了就不再转义 path。
    """
    height = config.TRANSCODE_HEIGHT if height is None else height
    crf = config.TRANSCODE_CRF if crf is None else crf
    preset = preset or config.TRANSCODE_PRESET
    head = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if start:
        head += ["-ss", f"{start:.3f}"]
    head.append("-i")
    tail = [
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale=-2:'min({height},ih)'",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        # 每秒一个关键帧：分片按关键帧切块
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]
    args = [shlex.quote(a) for a in head] + [path_expr or shlex.quote(path)]
    return " ".join(args + [shlex.quote(a) for a in tail])


def _chunks(chan):
    try:
        while True:
            data = chan.recv(CHUNK)
            if not data:
                break
            yield data
    finally:
        try:
            chan.close()
        except Exception:
            pass


def open_stream(session, path, start=0.0):
    """拉起远端 ffmpeg，返回 (首块, 后续生成器)；首块拿不到就抛 TranscodeError。"""
    q = shlex.quote(path)
    # 先按绝对路径找，找不到再按 $HOME 拼；两条合在一条命令里执行
    command = (
        f'P={q}; [ -e "$P" ] || P="$HOME"{q}; '
        + build_command(ensure_ffmpeg(session), path, start, path_expr='"$P"')
    )
    chan = session.ssh.get_transport().open_session(timeout=20)
    chan.settimeout(FIRST_BYTE_TIMEOUT)
    chan.exec_command(command)
    errors = bytearray()

    def drain():
        try:
            for line in chan.makefile_stderr("rb"):
                if len(errors) < 8192:
                    errors.extend(line)
        except Exception:
            pass

    threading.Thread(target=drain, daemon=True, name="transcode-stderr").start()
    try:
        first = chan.recv(CHUNK)
    except socket.timeout:
        chan.close()
        raise TranscodeError("转码迟迟没有输出（源文件可能损坏或编码不支持）")
    if not first:
        try:
            chan.recv_exit_status()
        except Exception:
            pass
        chan.close()
        message = errors.decode("utf-8", "replace").strip()
        raise TranscodeError(message or "转码失败")
    chan.settimeout(None)
    return first, _chunks(chan)
