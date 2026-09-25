# APK 正式签名落地指引（需要你自己动手的部分）

> 代码侧已经全部就绪：`tools/make-apk-keystore.ps1`（生成密钥）、
> `.github/workflows/release-apk.yml`（带四个前置自检的正式构建）、
> `android/app/build.gradle`（只有 secrets 到位才走 release 签名）。
> 剩下的每一步都需要**在你自己的机器和 GitHub 账号**上完成——签名私钥只要经过
> 任何第三方（包括本项目的数字员工）之手，它就不再是你的独占身份，所以这份
> 指引只描述"你做什么"，不代你做。

## 0. 为什么必须一次性做对

debug 签名的 APK 每台构建机各自现造密钥：v0.15 和 v0.16 是两个不相干的"作者"，
手机不允许覆盖升级，自动更新通道因此永远打不通。正式签名把密钥钉死成一份
`release.jks`——从此所有版本同源可升级。代价是反向也成立：**这份密钥丢了，
以后每个新版本对老用户都装不上**，只能恳求所有人卸载重装。所以第 2 步的备份
不是可选项。

## 1. 在你自己的 Windows 机器上生成密钥

打开 PowerShell，在仓库根目录跑：

```powershell
.\tools\make-apk-keystore.ps1
```

- 默认输出到 `%USERPROFILE%\ai-assistant-keystore\`，含两样东西：
  `release.jks`（密钥本体）和 `secrets-to-paste.txt`（四个待贴的值）。
- 两条口令由脚本用系统强随机源自动生成（约 195 比特），**你不需要记也不需要
  输入任何口令**——一切以 `secrets-to-paste.txt` 里的那份为准。
- 脚本会拒绝覆盖已存在的 `release.jks`（那是所有已发版本的身份，防手滑重造）。
- 跑完会打印一行**证书指纹**（`Certificate: ...`）：抄下来，将来核对"装到手机
  上的 APK 是不是这把钥匙签的"就靠它。
- 四个值只写进 `secrets-to-paste.txt`，不会出现在终端回显里——这是脚本
  故意的设计，别在公开会议共享屏幕上打开这个文件。可选参数 `-Dir` 与
  `-Alias` 能改输出目录和别名，默认值就对了，没必要动。

## 2. 备份（丢了就全完，做完这步再往下）

把 `release.jks` 和 `secrets-to-paste.txt` 一起，至少复制到**一个离线处**：
加密 U 盘、或私有网盘的加密压缩包。检查两件事：

- 只有你和指定继承的人能打开（脚本已把 NTFS 权限收成仅当前用户，复制出去
  可能被放宽，落盘处自己看一眼）；
- **永远不要把这两个文件提交进仓库**——仓库里 `tools/secret_scan.py` 会在
  CI 上拦住明显的密钥文件，但别赌它，私钥入库的那一刻就要当作已泄露处理。

## 3. 把四个值贴进 GitHub Secrets

仓库页面 → Settings → Secrets and variables → Actions → New repository secret，
逐条新建（名字**一字不差**，值从 `secrets-to-paste.txt` 整行复制，粘贴后别手贱
删首尾看起来"多余"的空格以外的字符）：

| Secret 名 | 内容 |
|---|---|
| `APK_KEYSTORE_BASE64` | 那串 Base64（可能很长，一整行，不能掉字） |
| `APK_KEYSTORE_PASSWORD` | keystore 口令 |
| `APK_KEY_ALIAS` | 密钥别名 |
| `APK_KEY_PASSWORD` | key 口令（当前生成器里与 store 口令相同） |

## 4. 触发一次正式构建

二选一：

- **发版即签**：打上版本 tag 并推送，例如
  `git tag v0.17.0 && git push origin v0.17.0`；
- **先试跑**：Actions 页面 → 「Release Android APK」→ Run workflow（`workflow_dispatch`）。

工作流开工先做签名密钥自检：三个步骤名就叫 `SIGNING KEY MISSING / CORRUPTED /
UNREADABLE`，全是条件步——该红才红，三条都没红就是密钥可用。红了对照下表修：

| 红的第几步 | 意思 | 修法 |
|---|---|---|
| `SIGNING KEY MISSING` | 一个 secret 都没贴 | 回第 3 步 |
| `SIGNING KEY CORRUPTED` | Base64 掉字/多换行 | 重贴 `APK_KEYSTORE_BASE64` 整行 |
| `SIGNING KEY UNREADABLE` | 口令或别名不对 | 核对 `_PASSWORD` / `_ALIAS` 三条 |
| （构建期）gradle 报签名失败 | key 口令单独不对 | 重贴 `APK_KEY_PASSWORD` |

全绿后它跑 `assembleRelease`，签好名的 APK 挂进对应 tag 的 GitHub Release
（无 tag 触发时以 artifact 形式给出），手机端那张"发现版本更新"卡片的
latest 数据也一并更新。tag 触发还有一道前置：tag 去掉 `v` 后必须与
`build.gradle` 的 `versionName` 一字不差，对不上直接红给你看。

## 5. 验证

1. 构建日志里三条 `SIGNING KEY ...` 条件步都没有被触发变红；
2. 从 Release 下载 APK，装前核对指纹（本机装过 JDK 的话）：
   `keytool -printcert -jarfile app-release.apk`，指纹应与第 1 步抄下的一致；
3. 装到手机、打开、登录一次——旧 debug 版要先卸载（见下条），新装后
   设置 → 检查更新 应报"已是最新"；
4. 发一个小版本号（比如 v0.17.1）走完整 tag 流程，确认**不卸载也能覆盖升级**
   ——这一步过了，正式签名才算真正落地。

## 6. 给现有用户的一句话公告模板

> 从本版本起 App 换用正式签名，与之前的测试版不兼容：请先卸载旧版
> （聊天记录和账号都在服务器上，卸载不丢），再点这里安装新版 → [链接]。
> 以后所有更新直接在 App 里点「升级」即可，不用再折腾。
