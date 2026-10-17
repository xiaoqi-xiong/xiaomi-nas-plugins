# 安装与回退

这些文件适用于已经具备小米原生认证 CGI 插件执行环境的设备。源码包不是官方商店插件包，也没有通用的一键安装器。不同固件的注册机制可能不同；未核实原生插件宿主时不要写注册数据库或执行历史 Root 脚本。若 SSH 或初始 Root 入口不确定，先阅读 [Root 与 SSH：发现、备份、恢复和回退](ROOT-SSH.md)，不要把插件部署当作恢复 Root 的方法。

## 先核实环境

1. 先按 [ROOT-SSH.md](ROOT-SSH.md) 确认现有授权的 SSH/管理入口可用，记录固件版本与当前插件注册文件位置；没有可靠入口时暂停部署。
2. 只读检查现有服务端口、owner/UID、插件根目录、服务挂载和可用容量。
3. 备份现有插件源码与注册项，私有配置单独保存。保留旧版本以便回退。
4. 不停止下载、移动媒体、重建现有容器或修改路由来安装这些网页。

## 准备文件

在电脑上运行测试与打包：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build_bundle.py --output /tmp/xiaomi-nas-plugins-source.tar.gz
```

把源码解压到新的版本目录，保持 `settings.py` 与各插件目录的相对关系。`settings.example.json` 复制为私有 `settings.json`，按真实环境填写 owner、owner_uid、nas_host、端口和 plugin_namespace。`ports.songloft` 默认指向本机 58091；尚未部署 Songloft 会显示离线，可设为 null 使其明确显示未配置。

私有目录仅服务用户可访问（目录 0700、文件 0600）。已有 `private/` 和 settings 不从公共包覆盖。Mihomo 密钥文件为 `private/controller.json`，形如 `{"secret":"YOUR_LOCAL_SECRET"}`。所有凭据、日志、数据库和订阅地址均不放进源码。

## 注册入口

以已验证的原生宿主注册方式映射 `deployment/plugins.json` 的 handler、入口和 PNG 图标。该 JSON 只是目录格式，不要直接当作厂商注册配置。

- `resources` 指向 MediaStack，传入 `NATIVE_PLUGIN_ID=resources`，入口必须是 `/center/index.html` 或 `/center/`，保证相对资源解析正确。
- MoviePilot 保留原 `mediastack` ID 和根入口。
- `resource-monitor` 与 `docker-monitor` 指向同一 monitor CGI，以不同 hash 打开标签；原生注册使用目录中的 `native_id`：`sysmon`、`dockermon`。源码逻辑 ID 保持原样，避免厂商分发器拒绝带连字符的原生 ID。
- 音乐使用独立 music CGI；Songloft 服务按 [MUSIC.md](MUSIC.md) 单独部署。
- 原 `qbpt`、`qbpublic`、`pansou` 注册别名可保留。详情见架构文档。

宿主必须在执行 CGI 前验证原生用户会话，并以配置 owner/UID 执行。检查 USER/UID 只是第二道限制，不是通用网站的登录认证。不要使用公开 HTTP CGI 服务器代替原生认证宿主。

## 验证后再切换

先在测试入口验证 owner 拒绝、三服务切换、错误重试、原生返回。之后检查真实任务数、保存路径和限速与更新前一致，PT 无代理、公共下载规则不变。监控等至少两个样本再验证速率，检查 Docker I/O 标为累计值；缺失传感器正常显示不可用。

音乐先验证服务健康与账号登录，再扫描用户指定只读曲库，实际播放、拖动和切歌。模拟音频成功不等于真实音乐库和原生客户端通过。未经验证不要宣称手机后台播放或音箱控制支持。

确认新入口可用后再替换旧源码引用，保留旧目录与私有状态。遇到问题将注册项恢复到旧目录；新增 Songloft 可单独停止，保留曲库和数据目录，不影响下载器。

## 固件或地址变化

DHCP 保留地址能避免 IP 改变，但不保证 SSH 和插件在固件升级后仍存在。先查当前租约和端口，再按 [ROOT-SSH.md](ROOT-SSH.md) 区分地址变化、已有 SSH 恢复和初次 Root 入口。没有可靠入口时暂停部署；不要盲目重刷、降级、修改安全标志或删除现有数据。
