import subprocess
from app.config import KEYCHAIN_SERVICE


def set_password(account, password):
    """security add-generic-password -a <account> -s <service> -w <password>"""
    subprocess.run(
        ["security", "add-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w", password],
        check=True, capture_output=True, text=True,
    )


def get_password(account):
    """security find-generic-password -a <account> -s <service> -w（-w 直接输出密码）"""
    r = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def delete_password(account):
    subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE],
        capture_output=True, text=True,
    )


def has_password(account):
    return get_password(account) is not None
