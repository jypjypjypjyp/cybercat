import app.ssh_config as sc


SAMPLE = """
# My hosts
Host cloud
    HostName 192.0.2.20
    User root
    IdentityFile ~/.ssh/id_rsa

Host host-a
    HostName 192.0.2.10
    Port 2222
    User alice
    IdentityFile ~/.ssh/id_rsa

Host *
    HostKeyAlgorithms +ssh-rsa
    PubkeyAcceptedAlgorithms +ssh-rsa
"""


def test_parse_basic_and_skip_wildcard():
    hosts = sc.parse_ssh_config(SAMPLE)
    aliases = [h.alias for h in hosts]
    assert aliases == ["cloud", "host-a"]  # Host * 被跳过
    # cloud 默认端口 22
    cloud = hosts[0]
    assert cloud.hostname == "192.0.2.20"
    assert cloud.port == 22
    assert cloud.user == "root"
    assert cloud.identity_file == "~/.ssh/id_rsa"
    # host-a 自定义端口
    h202 = hosts[1]
    assert h202.port == 2222
    assert h202.user == "alice"


def test_parse_skips_missing_hostname():
    text = "Host ghost\n    Port 2222\nHost real\n    HostName 1.2.3.4\n"
    hosts = sc.parse_ssh_config(text)
    assert [h.alias for h in hosts] == ["real"]


def test_to_bookmark_key_method():
    hosts = sc.parse_ssh_config("Host h\n    HostName 1.2.3.4\n    IdentityFile ~/.ssh/id_ed25519\n")
    bm = hosts[0].to_bookmark()
    assert bm["auth_method"] == "key"
    assert bm["key_path"] == "~/.ssh/id_ed25519"
    assert bm["port"] == 22


def test_to_bookmark_password_method_when_no_key():
    hosts = sc.parse_ssh_config("Host h\n    HostName 1.2.3.4\n    User u\n")
    bm = hosts[0].to_bookmark()
    assert bm["auth_method"] == "password"
    assert bm["username"] == "u"
