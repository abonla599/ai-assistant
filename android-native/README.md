# android-native — 混合 App 原生工程（主体原生 + 少量 WebView）

Kotlin + Jetpack Compose 编写的 Android 客户端，与既有资产的关系：

| 形态 | 位置 | 说明 |
|------|------|------|
| 浏览器网页版 | `backend/app/web/static` | 原样保留，不受本工程影响 |
| WebView 壳 App | `android/` | 旧形态，保留可继续发布 |
| **混合 App（本工程）** | `android-native/` | 主体界面原生，仅"设置与管理"一页内嵌 WebView 复用 Web 前端 |

## 界面构成（主体原生）

- 登录 / 注册 / 找回密码（三题与后端 `RECOVERY_QUESTIONS` 同源）
- 会话列表：新建、删除（长按）
- 聊天：`/v1/chat/stream` 流式渲染、停止、失败回退非流式、图片附件上传、
  模型下拉（`/v1/models`，默认取服务端 `default`）、答复反馈（`/v1/feedback`）
- 我的记忆：列表 / 搜索 / 添加 / 删除
- 网页版·设置与管理（唯一的 WebView 页）：加载 `/app/`，用原生持有的 Bearer
  令牌调 `/v1/auth/adopt` 换取 httpOnly 会话 Cookie 后重载——模型服务配置、
  管理面等复杂表单零重写复用 Web 实现

## 凭据与网络

- 只用 `Authorization: Bearer`（头凭据），不依赖 Cookie、无需 CSRF 头；
  令牌存本机私有 SharedPreferences。
- 服务地址默认 `BuildConfig.DEFAULT_BASE_URL`（与壳同源主机名），登录页可改。
- 与后端契约逐字对齐：SSE 帧 `data: {json}\n\n`、事件 start/content/done/error。

## 构建

本机无 Android SDK（与壳同一策略）：推分支由 CI
（`.github/workflows/build-native-apk.yml`，Gradle 8.7 + runner 自带 SDK）出
debug APK；release 签名沿用壳的 `APK_KEYSTORE*` 环境变量约定，但
applicationId 为 `xyz.fenever.assistant.native`，与壳可并存安装。

```
gradle -p android-native assembleDebug   # CI 内执行
```
