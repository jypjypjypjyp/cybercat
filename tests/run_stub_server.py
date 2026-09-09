# 长驻本地 SFTP stub server，供浏览器端到端验收连接
import sys, os, time
from tests.sftp_stub_server import run_sftp_server

port, root, key = run_sftp_server()
# 往 root 里放点测试内容
os.makedirs(os.path.join(root, "docs"), exist_ok=True)
with open(os.path.join(root, "README.md"), "w") as f:
    f.write("# stub root\nhello world")
with open(os.path.join(root, "data.csv"), "w") as f:
    f.write("id,name\n1,alice\n2,bob")
with open(os.path.join(root, "docs", "notes.txt"), "w") as f:
    f.write("some notes")

# 打印连接信息，方便验收
print(f"STUB_PORT={port}")
print(f"STUB_ROOT={root}")
sys.stdout.flush()
# 保活
while True:
    time.sleep(60)
