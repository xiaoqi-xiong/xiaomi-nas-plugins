# 小米智能存储社区插件 / Xiaomi NAS Plugins

面向小米智能存储的移动端网页插件。把盘搜与两套下载器放在一个入口，并提供独立的 MoviePilot、代理、系统监控、Docker 状态和音乐页面。

| 插件 | 功能 | 依赖 |
| --- | --- | --- |
| 搜索下载 | 盘搜搜索、选择下载器提交磁力、PT/公共下载任务与配置 | PanSou + 两个独立 qBittorrent |
| MoviePilot | 站点搜索、订阅、自动整理 | 已配置的 MoviePilot |
| 代理管理 | 查看连接、切换允许的代理组 | 本机 Mihomo 控制器 |
| 系统监控 | 设备/CPU/内存/固件信息，网络、存储、可用温度与采样趋势 | Linux `/proc`、`/sys` |
| Docker | 容器状态、资源占用、端口、Compose 分组与搜索 | 已有权限的 Docker CLI，仅查看 |
| 音乐 | Songloft 登录、曲库搜索、音频播放与拖动 | 独立 Songloft 服务 |
| M-Team 官网 | 站点网页入口 | 自行登录官方站点 |

**当前发布的是源码和部署模板。** 本地测试不能代替 NAS 实机验收。新版固件可能关闭原有 SSH/插件入口；这个项目不包含 Root、降级或固件绕过工具，也不自动恢复这些入口。只有已经具备受信任插件执行环境的设备才能部署 CGI。

## 开始使用

- [Root 与 SSH：发现、备份、恢复和回退](docs/ROOT-SSH.md)
- [安装边界、配置和回退](docs/INSTALL.md)
- [入口、路由与服务架构](PLUGIN-ARCHITECTURE.md)
- [Songloft 部署和音乐验证](docs/MUSIC.md)
- [设计参考与验收范围](docs/REFERENCE-DESIGN.md)
- [安全说明](SECURITY.md)

搜索下载通过一个页面切换三项服务，**两个 qB 进程仍独立**，保留各自任务、账号、路径和限速。PT 保持直连；公共下载可按自己的配置使用代理；小米原生服务不经过这里的代理。页面入口不会自动修改这些网络策略。

系统监控每 5 秒采样，Docker 每 15 秒查询。首次采样、计数器重置或重启后的速率显示为不可用，随后才显示实际差值。Docker 网络与磁盘 I/O 数字为累计值。没有温度或 Docker 访问权限时会明确显示缺失，不申请额外权限。

各插件外壳共用可拖动的圆形返回按钮，松手后吸附屏幕边缘，点击调用小米原生返回接口。它位于内嵌网页之外，不受 MoviePilot 自己的返回按钮影响。普通浏览器没有小米接口时会给出提示；实机原生返回仍须在支持的固件上验收。

## 目录

```text
settings.py / settings.example.json  共享配置及通用模板
mediastack/                         搜索下载、qB/PanSou UI、MoviePilot 网关
resource-monitor/                   主机与 Docker 只读面板
music/                              Songloft 网关和音乐界面
xproxy/ / mteam/                     代理与站点入口
assets/                             本项目图标
deployment/                        入口目录、Songloft Compose 模板
scripts/                            源码打包与本地模拟预览
tests/                              网关、权限、路由和采样测试
```

`settings.json`、`private/`、`.env` 都是本机私有文件，不进入版本库。第三方服务与数据库不包含在源码包中。PanSou 页面来自自己的 PanSou 服务；可选 VueTorrent 需要另行遵守上游许可安装。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_monitor_ui.js
python3 scripts/build_bundle.py --output /tmp/xiaomi-nas-plugins-source.tar.gz
```

打包命令仅生成带 SHA-256 清单的源码包，拒绝覆盖已有输出；它不是小米官方插件安装包，不会上传、注册、启动或修改服务。

## 访问与隐私

CGI 依赖小米宿主先验证原生登录，再校验配置的 Linux 用户名和 UID。不要把 CGI 放进一个以同一账号运行、却没有前置认证的通用 Web 服务器，否则账号检查不等于访客认证。不要直接向公网开放服务端口。

网关只访问本机配置的固定服务。自动登录资料位于 NAS 私有目录，代理密钥只从私有配置读取。公共源码不得包含密码、Cookie、API Key、订阅链接、用户媒体或设备运行日志。

## 许可

本项目原创代码和图标使用 MIT License。小米及各上游项目的名称和商标归原权利方所有；本项目不代表官方。外部服务和另行安装的前端仍遵循各自许可。
