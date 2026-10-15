# 插件与服务架构

一个入口可以聚合多个网页，后端服务仍各自运行。入口清单在 `deployment/plugins.json`；它是本项目的路由目录，并非小米官方安装清单。

| ID | 入口路径 | CGI | 行为 |
| --- | --- | --- | --- |
| resources | `/center/index.html#search` | `mediastack/mediastack.cgi` | 搜索、PT、公共下载三个标签 |
| mediastack | `/index.html#/` | 同上 | 原 MoviePilot 外壳 |
| resource-monitor | `/index.html#overview` | `resource-monitor/monitor.cgi` | 主机资源概览 |
| docker-monitor | `/index.html#docker` | 同上 | Docker 独立入口，共用监控实现 |
| music | `/index.html#/` | `music/music.cgi` | Songloft 曲库与播放 |
| xproxy | `/index.html#/` | `xproxy/xproxy.cgi` | Mihomo 管理 |
| mteam | `/index.html#/` | `mteam/mteam.cgi` | 官方站点网页 |

`qbpt`、`qbpublic`、`pansou` 旧别名保留，避免破坏书签与既有入口。新的 resources 注册传入 `NATIVE_PLUGIN_ID=resources`，并把相对于插件根目录的路径传给 MediaStack CGI；旧别名另外传 `NATIVE_APP_ROUTE=pt|public|pansou`。不要对新聚合入口设置该旧单服务变量。

搜索下载内部载入 `../pansou/vendor-ui/index.html`、`../pt/index.html?embedded=1`、`../public/index.html?embedded=1`。任务来源始终明确，磁力链接提交前选择下载器。三个 iframe 延迟创建并保留状态；网关错误页显示重试，不伪装成成功。MoviePilot 根路由保持原样。

## 原生返回与手机布局

`mediastack/native-bridge.js` 是所有外壳的共享返回实现。带 `data-xiaomi-shell` 的页面只安装一个 46px 圆形悬浮按钮；嵌入的 qB 页面不重复安装。旧 qB/PanSou 别名仍使用外壳承载返回。拖动后吸附左右边缘，并在当前会话记住位置；旋转和可视区域变化后重新限制位置，预留底部导航及播放器空间。

按钮调用 `normal_goback`，不使用网页 `history.back()`。没有原生桥接、调用失败或超时均有明确提示，超时后可再次点击。代理和 M-Team CGI 同样从共享文件提供此脚本，并保留 owner/UID 检查。

手机资源中心采用确定的视口高度和 flex 高度链，iframe 填满内容区并在内部滚动；隐藏 iframe 显式使用 `display:none`。设置面板允许纵向滚动，长任务名不会撑宽页面。修改这些布局时同时回归窄屏、竖屏、横屏和桌面。

## 数据与网络

两套 qB 使用各自固定端口、任务数据库、保存路径和凭据。合并页面不会合并数据库、转移做种或改变限速。PT 直连、公共下载的代理政策由现有下载器配置控制。各实例限速相加才是总速率。

MoviePilot 负责已配置的站点、下载和整理。搜索下载只是操作入口，不会自动把任意资源提交给 MoviePilot。插件代码更新不调整已有媒体目录、下载分类或硬链接。

## 监控

总览系统信息包含设备型号、CPU 型号、核心数、内存总量、系统/固件版本、架构、内核与运行时间。固件版本仅显示经过语义验证的来源；读取不到时显示未知，不把 Linux 版本当设备固件。旧 resource-monitor ID 保持兼容。

API 保持 `monitor.cgi?api=status`、`monitor.cgi?api=docker`。宿主 5 秒采样，Docker 15 秒查询；不可见页面暂停轮询。CPU 使用排除 guest 重复计数的差值，网络和磁盘速率按单调时钟计算；跨重启、旧样本、负差值以及首次样本均不生成速率。温度和存储卷只显示能读取的值。

Docker 仅运行有超时和结果截断的固定 `ps` / `stats` 查询。面板支持筛选、搜索、Compose 分组与详情，不含启动、停止、删除、终端或部署按钮。不要为查看面板新增 Docker 管理权限。

## 音乐

Songloft 是单独的 ARM64 服务，网关固定连接本机端口，校验插件 owner/UID，隔离小米授权信息与 Songloft JWT。曲库目录只读，数据库存本地可写目录。播放按块转发并保留 Range 响应。没有服务时显示离线，不用假曲目填充生产页面。

手机浏览器播放、原生返回、后台播放、电视与 MIoT 音箱是不同验收项。仅有 Compose、官方 API 或本地音频模拟不能证明音箱兼容；音箱插件须另外核验对应型号和上游要求。

## 验收界限

单元测试检查权限、白名单、采样和路由；模拟预览检查布局、交互与错误状态。上线还必须验证原生插件注册、真实服务会话、qB/盘搜联通、Songloft 扫描及播放、原生返回。固件升级后若入口消失，先确认受支持访问方式，不能把源码上传视为部署完成。
