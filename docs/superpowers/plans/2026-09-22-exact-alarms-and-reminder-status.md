# v0.18：精准闹钟 + 把"到点到底响没响"做成看得见的一行

日期 2026-09-22 · 分支 develop · 触发：用户报告"设过的提醒从来没响过，只是后来想起来才发现时间过了很久"

## 0. 已排除与剩下的假设

代码侧排除四条（逐条读过，非推断）：闹钟确实排了（`ShellBridge.scheduleReminder` → `ReminderScheduler`）、
时间换算对（`app.js:1677` 交本地时区 epoch 毫秒，`ReminderDraft` 只认合理正毫秒）、通知渠道确实建了
（`MainActivity:109` → `NotificationChannels.ensure`）、通知权限确实在新增提醒时申请过
（`ShellBridge:260` → `askNotificationPermissionOnce`）。

剩下两条，症状一模一样且都只能由设备侧证：① 通知授权被拒之后 `ReminderReceiver:32` 直接 `return`，
零痕迹；② 国产 ROM 省电把 `setAndAllowWhileIdle` 的广播拖到几小时之后。用户拍板：**申请精准闹钟权限，
并把三态做成可见**（①②谁中招，屏幕上都能读出来）。

## 1. 决定与代价

- 权限用 `SCHEDULE_EXACT_ALARM`，不用 `USE_EXACT_ALARM`。理由是可诊断性：前者能用
  `canScheduleExactAlarms()` 读出真/假并导向系统那一页；后者拿不到授权是静默的，属于本次要消灭的那一类。
- 拿不到精确闹钟授权时**回退**到现在的 `setAndAllowWhileIdle`，绝不因为没授权就设不上提醒。
- 三态常驻一行，默认 `hidden`、由 JS 填（照 `index.html:217` + `test_web_pwa.py:2370-2385` 那个现成形状），
  内容只有状态与两个"去设置"入口，不写结论句——结论留给读的人，免得成为第二个事实来源。
- 本次**不**改通知渠道重要性（`IMPORTANCE_DEFAULT`）。理由：它会改 spec §3:122 的陈述，而它不解决
  ①② 中的任何一个；先让状态可见，响度问题等有证据再动。

## 2. 分层与产物

| 落点 | 文件 | 判据 |
|---|---|---|
| 档位决策（纯逻辑，必须能被 JVM 跑到） | 新 `core/AlarmPolicy.java` | 新增 `AlarmPolicyTest`：能排精确就排精确；不能就退 `WHILE_IDLE`；SDK<31 恒为可排 |
| 记录形状加两个可变计数 | `core/Reminder.java`、`core/ReminderStore.java` | `ReminderStoreTest` 加一条**逐字段往返**断言：`flush()` 写了几个键，`load()` 就必须读回几个（只加 load 不加 flush 是本项目最贵的静默丢法） |
| 排期与到点 | `ReminderScheduler`、`ReminderReceiver`、`AndroidManifest.xml` | 台架编不到（只编 `core/`）→ 靠源码形状锁 `test_android_shell.py` + CI `assembleDebug` + 真机 |
| 桥面 | `ShellBridge.capabilities()` 加 `notifications`/`exactAlarms`；`listReminders()` 每行加 `firedAt`/`missed`；新方法 `openSettings(json)`（白名单两种 target） | `test_web_pwa.py`  arity 锁与回包表；**并把"八个方法"那句话改成从源码派生**（见 §3） |
| 网页 | `shell.js`（`capabilities()` 改为每次现问，不再是加载时快照）、`app.js` 提醒页顶部一行、`index.html` 常驻 markup | `test_web_pwa.py:83-94`（`$("id")` 必须在 markup 里）、`:1934`（禁手写版本号字面量）、`:2370-2385`（默认藏 + 不许 innerHTML） |

## 3. 顺手修掉的一处既有谎

`ShellBridge.java:23` 与 `shell.js:8` 都写"八个方法一字不差"，而 `checkUpdate`（v0.16 加的）已是第九个；
唯一的锁 `test_web_pwa.py:1608` 判据是"每个名字至少出现一次"——加方法不会红。改成：从
`ShellBridge.java` 里扫 `@JavascriptInterface` 的方法名集合，断言它等于 `shell.js` 实际调用的集合，
并把散文里的数字去掉（"清单逐字对齐由这把锁负责"）。这样第 10 个方法进来也不会先谎后红。

## 4. 会被这次改动变成谎话、必须同批改的地方（调查穷尽结果）

- `AndroidManifest.xml:17-18`（"不申请 SCHEDULE_EXACT_ALARM…产品不承诺准点"）
- `ReminderScheduler.java:12-15`（"既不申请特权，也不做任何降级分支"）
- `ReminderReceiver.java:33-35`（"这里连表都不动"——现在要落账）
- `ShellBridge.java:348-351`（"不需要额外的『问过了』标记"）
- `MainActivity:649-662`（`onRequestPermissionsResult` 只接相机，1003 落空）
- `docs/superpowers/specs/2026-09-19-shell-native-capabilities-design.md` §1:55-59、§2:63-75、§4:111-122、§6
- `docs/superpowers/plans/2026-09-19-shell-native-capabilities.md:19-21`（历史计划件：加一行"本节决定已于
  2026-09-22 被 v0.18 推翻，现行口径看 spec §1  amendment"，不重写历史）
- `docs/用户手册.md:34-35`、`.github/workflows/release-apk.yml:197-198`（每一份 Release 正文的尾巴）
- `backend/app/web/site/index.html:70`（"不获取精准闹钟权限"）→ 改完必须**重建 EXE** 才上线
- 新建 `docs/releases/v0.18.md`（`test_release_notes.py:26-32`：`versionName` 一涨就要求它存在）

## 5. 顺序

1. `core/AlarmPolicy` + `Reminder`/`ReminderStore` 两个字段 —— 先写 JVM 红的测试。
2. Manifest + `ReminderScheduler` + `ReminderReceiver` 记账 + `MainActivity` 回调 + `openSettings`。
3. `shell.js` / `app.js` / `index.html` 那一行 + pytest 锁（含 §3 那把改派的锁）。
4. 全量 pytest + `venv/Scripts/python.exe tools/shell_jvm_tests.py`（退出码 2 = 根本没跑成，不算绿）
   + CI（`android-tests.yml` 由 `push.paths: android/**` 触发）。
5. 文案与文档一次性扫平（§4 清单逐条打勾），`build.gradle` 升 0.18 / versionCode 18，
   打 `v0.18` tag 触发 `release-apk.yml`，写 `docs/releases/v0.18.md`。
6. 重建 EXE 让官网文案上线；壳的改动**只能由他真机验收**：设一条 2 分钟后的提醒 → 看那一行怎么变、
   锁屏响不响、以及"上次实际到点"与预定时间差多少。

## 6. 不在本版

通知渠道重要性、`USE_EXACT_ALARM`、厂商白名单引导页、`once` 错过之后的补发策略（今天 `once` 在没授权时
那条广播一旦错过就永远不响——本版只把它变成**看得见**，不改"补不补"，避免顺手塞进没被批准的行为）。
