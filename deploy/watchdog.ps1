#Requires -Version 5.1
<#
==============================================================================
AI 智能助手 —— 崩溃守护看门狗
（单次巡检：进程不在就拉起，拉起就记账，连续失败会熔断）
==============================================================================

它补的是 2026-09-19 那次事故的洞：run_backend.exe（PID 89476）在 11:59:31 之
后消失，data/backend.log 里没有任何 "Shutting down" / "Finished server process"
——正常退出流程一步都没走，就是被硬杀或崩了。而登录自启那条快捷方式
（shell:startup → tools/start-ai-stack.bat）只在【登录那一刻】跑一次，跑完就退
场，之后没有任何人负责"进程没了再拉起来"。下一次有进程是 14:39:02（PID
111764，人工重启），中间约 2.5 小时 ai.fenever 域名是无人知晓的停机窗口。

四条设计取舍，改这个脚本前请先读：

1) 单次巡检、不常驻。每次运行只"看一眼 → 必要时拉一把 → 记一行日志 → 退出"。
   看门狗自己常驻的话，它死了谁盯？交给计划任务每分钟唤醒一次，"谁守护守护
   进程"外包给操作系统，链路最短，也天然被限了速。
2) 只启动，从不杀进程。本脚本不会 Stop 任何东西，对正在服务好友的进程零干扰。
3) 判定"活着"看**可执行文件全路径 / 命令行**，不只进程名。隧道那条尤其重要：
   本机此刻还挂着两个 quick 隧道的 cloudflared 残留进程，只按名字找会让命名
   隧道的死活被它们盖掉。
4) 拉起要计预算（默认每小时 3 次）。exe 本身坏了会"起来就崩"，不限次就是一分钟
   一次的无限重启风暴。到上限就停手并把话说清楚，等人来看——守护的底线是
   别把故障放大成日志洪水。

日志：data\watchdog.log（超 1MB 滚一份 .1）
状态：data\watchdog-state.json（拉起预算 + 告警节流时间戳）
两个文件都在 data/ 下，而 data/ 已经整体 gitignore，不会污染仓库。

------------------------------------------------------------------------------
用法
------------------------------------------------------------------------------
  # 空跑：只判断、只往控制台打印，不拉起、不写日志、不写状态。现网在跑也安全。
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\34426\ai-assistant\deploy\watchdog.ps1 -DryRun

  # 真跑一次（想确认它确实拉得起来时用）
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\34426\ai-assistant\deploy\watchdog.ps1

------------------------------------------------------------------------------
注册成计划任务（每分钟一次；登录型任务，不用存密码）—— 请本人确认后执行
------------------------------------------------------------------------------
  schtasks /Create /F /TN "AI助手-崩溃守护" /SC MINUTE /MO 1 ^
    /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\34426\ai-assistant\deploy\watchdog.ps1"

  schtasks /Query /TN "AI助手-崩溃守护" /V /FO LIST     # 看它注册成什么样
  schtasks /Run     /TN "AI助手-崩溃守护"               # 立刻手动触发一次
  schtasks /Delete  /TN "AI助手-崩溃守护" /F            # 卸载

同一件事的 PowerShell 写法（能把"hidden / 错过就补跑 / 不许并发"表达得更准）：

  $act  = New-ScheduledTaskAction -Execute 'powershell.exe' `
          -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\34426\ai-assistant\deploy\watchdog.ps1'
  $trig = New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes 1)
  $set  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
          -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
  $prin = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive
  Register-ScheduledTask -TaskName 'AI助手-崩溃守护' -Action $act -Trigger $trig -Settings $set -Principal $prin

（-MultipleInstances IgnoreNew 与脚本里的单实例锁是同一件事的两道保险，见下面主流程注释。）

注意：计划任务指向的是**仓库里的这个 .ps1**，不是 dist\ 里的副本，理由见文件末尾注释。
#>
[CmdletBinding()]
param(
    # 项目根：与 tools/start-ai-stack.bat 里 `cd /d` 的那个路径同一个事实来源。
    [string] $ProjectRoot = 'C:\Users\34426\ai-assistant',

    # 后端监听端口。进程在但端口没监听时只告警、不重启（见下面 Test-PortListening 的注释）。
    [int] $Port = 8000,

    # 熔断阈值：同一目标一小时内最多自动拉起几次。
    [int] $MaxRestartsPerHour = 3,

    # 拉起后多少秒内没看到进程就算这次失败（PyInstaller onedir 的 exe 起不来会很快退出）。
    [int] $StartGraceSec = 15,

    # Cloudflare 命名隧道名（tools/start-ai-stack.bat 里 `tunnel run` 后面那个）。
    [string] $TunnelName = 'ai-assistant',

    # 只管后端、不管隧道时用这个（隧道归另一套守护时）。
    [switch] $SkipTunnel,

    # 空跑：不拉起、不写日志、不写状态，只把判断结果打到控制台。
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'

if ($DryRun) {
    # 空跑是人在控制台跟前看的。控制台默认按 GBK 解释字节，中文会糊成一团，
    # 这里把它切到 UTF-8（start_ai.bat 用的 chcp 65001 就是这个意思）。
    # 真跑时不动它：计划任务里没有控制台给人看。
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
}

# ---------------------------------------------------------------- 路径与常量
$BackendExe      = Join-Path $ProjectRoot 'dist\run_backend\run_backend.exe'
$TunnelExe       = Join-Path $ProjectRoot 'tools\cloudflared.exe'
$DataDir         = Join-Path $ProjectRoot 'data'
$LogPath         = Join-Path $DataDir 'watchdog.log'
$StatePath       = Join-Path $DataDir 'watchdog-state.json'
$LogMaxBytes     = 1MB
$WarnRepeatMins  = 15   # 同一条告警最多每 15 分钟说一次，别把真信号埋进重复行

# ------------------------------------------------------------------ 日志函数
function Write-Log {
    param([string] $Level, [string] $Message)

    if ($DryRun) {
        Write-Host ('[{0}] {1}' -f $Level, $Message)
        return
    }
    $line = '[{0}] {1,-7} {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    try {
        if (-not (Test-Path -LiteralPath $DataDir)) {
            New-Item -ItemType Directory -Path $DataDir | Out-Null
        }
        # 滚存方式与 run_backend.py 里那套一致：超 1MB 先转成 .1，无人清理也不会无限长。
        if ((Test-Path -LiteralPath $LogPath) -and
            ((Get-Item -LiteralPath $LogPath).Length -gt $LogMaxBytes)) {
            Move-Item -LiteralPath $LogPath -Destination "$LogPath.1" -Force
        }
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    } catch {
        # 日志写不下去不该让守护本身停摆，退化成控制台输出。
        Write-Host ('看门狗日志写失败（{0}）：{1}' -f $_.Exception.Message, $line)
    }
}

# ---------------------------------------------------- 状态（预算 + 告警节流）
function Read-State {
    # 返回 hashtable。文件坏掉/不存在都按"空账"处理：宁可多给一次重启机会，
    # 也不要因为一个 JSON 逗号让守护从此不再拉人。
    $result = @{}
    if (-not (Test-Path -LiteralPath $StatePath)) { return $result }
    try {
        $raw = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($raw)) { return $result }
        foreach ($prop in (ConvertFrom-Json $raw).PSObject.Properties) {
            # @() 包一层是必要的：JSON 里只剩一条记录时 ConvertFrom-Json 给的是标量字符串，
            # 后面所有按数组处理的地方都会把它当成"一串可拼接的字符"来对待。
            $result[$prop.Name] = @($prop.Value)
        }
    } catch {
        Write-Log 'WARN' ('状态文件读不了，按空账重新开始：{0}' -f $_.Exception.Message)
    }
    return $result
}

function Save-State {
    param([hashtable] $State)
    if ($DryRun) { return }
    try {
        $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatePath -Encoding UTF8
    } catch {
        Write-Log 'WARN' ('状态文件写不下去：{0}' -f $_.Exception.Message)
    }
}

function Get-LaunchCount {
    # 只数"最近 1 小时内"的拉起次数，更早的不作数——服务自己稳了就该重新拿到额度。
    # 故意只返回一个数字：标量在函数返回时不会被摊平，天然安全。
    param([hashtable] $State, [string] $Key)
    $cutoff = (Get-Date).AddHours(-1)
    $count = 0
    foreach ($item in @($State[$Key])) {
        $t = [datetime]::MinValue
        if ($item -and [datetime]::TryParse([string]$item, [ref]$t) -and $t -gt $cutoff) { $count++ }
    }
    return $count
}

function Add-LaunchRecord {
    # 剪掉过期旧账 + 记下这一次。全程直接改 hashtable，绝不把数组当返回值传来传去：
    # PowerShell 会把函数返回的单元素数组摊平成标量，而 标量 + 标量 是**字符串拼接**
    # 不是数组追加 —— 实测过一次就写出 "2026-..+08:002026-..+08:00" 这种一条假记录，
    # 于是预算恒为 0、熔断永不触发，看门狗最重要的一条保护被静默拆掉。
    param([hashtable] $State, [string] $Key)
    $cutoff = (Get-Date).AddHours(-1)
    $list = New-Object System.Collections.ArrayList
    foreach ($item in @($State[$Key])) {
        $t = [datetime]::MinValue
        if ($item -and [datetime]::TryParse([string]$item, [ref]$t) -and $t -gt $cutoff) {
            [void]$list.Add([string]$item)
        }
    }
    [void]$list.Add((Get-Date).ToString('o'))
    $State[$Key] = $list.ToArray()   # 赋值不过管道，数组还是数组
}

function Test-ShouldLog {
    # 告警节流：同一件事每分钟刷一条，真告警就会淹水。首次说、之后每 $WarnRepeatMins 分钟再说一次。
    param([hashtable] $State, [string] $Key)
    $last = [datetime]::MinValue
    $raw = [string]$State[$Key]
    if ($raw -and -not [datetime]::TryParse($raw, [ref]$last)) { $last = [datetime]::MinValue }
    if ($last -ne [datetime]::MinValue -and ((Get-Date) - $last).TotalMinutes -lt $WarnRepeatMins) {
        return $false
    }
    $State[$Key] = (Get-Date).ToString('o')
    return $true
}

# ------------------------------------------------------------------ 探活函数
function Test-PortListening {
    # 只用 TcpClient 连一下，不依赖 Get-NetTCPConnection（各版本 Windows 上不一定有）。
    # 用途有两个：拉起后确认真的在服务；以及发现"进程还在但端口不通"的卡死形状。
    param([int] $PortNumber)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $wait = $client.BeginConnect('127.0.0.1', $PortNumber, $null, $null)
        return ($wait.AsyncWaitHandle.WaitOne(700) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-BackendProcess {
    # 按 exe 全路径认，别只认进程名：别处一个同名 run_backend.exe 会让守护以为
    # 服务还活着，于是真的那一个死了它也不拉。
    Get-CimInstance Win32_Process -Filter "Name = 'run_backend.exe'" |
        Where-Object {
            $_.ExecutablePath -and
            ($_.ExecutablePath.TrimEnd('\') -ieq $BackendExe.TrimEnd('\'))
        }
}

function Get-TunnelProcess {
    param([string] $Name)
    # 必须匹配命令行里的 `tunnel run <name>`：机器上可能残留 quick 隧道
    # （`cloudflared.exe tunnel --url http://127.0.0.1:8000`），只按进程名找会把
    # 命名隧道的死活被残留进程盖掉。
    Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
        Where-Object { $_.CommandLine -match ('tunnel\s+run\s+' + [regex]::Escape($Name)) }
}

# -------------------------------------------------------- 核心：拉一把 + 记账
function Invoke-Guard {
    param(
        [hashtable] $State,
        [string] $Key,
        [string] $Title,
        [scriptblock] $Probe,   # 返回"活着"的进程对象；空 = 死了
        [scriptblock] $Launch,  # 返回 Start-Process -PassThru 的对象
        # 只在调用方明确要求时才探端口。隧道那条不许传：cloudflared 是往外连的，
        # 本机 8000 通不通跟它活没活着是两件事，拿后端的标准去量它会天天误报。
        [int] $CheckPort = 0
    )

    $probeResult = & $Probe
    $found = @($probeResult)
    if ($found.Count -gt 0) {
        # 活着时端口只用来"看一眼"，绝不因为端口一时不通去重启：
        # 冷启动/依赖加载慢的时候端口本来就还没起，重启会把一个只是慢的服务打断，
        # 而"事件循环被卡死"那种形状（524 那次）留证据比自动开刀更安全。
        $pids = ($found | ForEach-Object ProcessId) -join ', '
        $listening = $true
        if ($CheckPort -gt 0) { $listening = Test-PortListening -PortNumber $CheckPort }

        if (-not $listening) {
            if (Test-ShouldLog -State $State -Key ("seen:port:" + $Key)) {
                Write-Log 'WARN' ("$Title 进程在（PID $pids）但 127.0.0.1:$CheckPort 连不通，" +
                                  "可能是卡死或还在启动中。本条按 ${WarnRepeatMins} 分钟一次提醒，" +
                                  "守护不会自动重启它。")
            }
        } elseif ($DryRun) {
            # 空跑是给人当"现在到底什么状态"用的，这种时候要说清楚，别只回两行沉默。
            Write-Log 'INFO' ("$Title 存活：PID $pids" +
                              $(if ($CheckPort -gt 0) { "，127.0.0.1:$CheckPort 可连接" } else { '' }))
        }
        return
    }

    $recent = Get-LaunchCount -State $State -Key $Key
    if ($recent -ge $MaxRestartsPerHour) {
        # 熔断。一小时内已经拉了这么多次还不住，说明不是偶发崩溃，而是 exe 本身
        # 起不来（端口被占、缺文件、磁盘满、重建到一半）。继续一分钟一次地拉只会
        # 把故障放大成日志洪水，所以停手等人工。
        if (Test-ShouldLog -State $State -Key ("seen:cap:" + $Key)) {
            Write-Log 'ERROR' ("熔断：$Title 最近 1 小时内已拉起 $recent 次仍没活住，" +
                               "停止自动重启，需要人工介入。先看 data\backend.log 和 $LogPath。" +
                               "确认排查完想恢复自动守护：删掉 $StatePath 里的 '$Key' 那一项" +
                               "（或整个文件删掉，等于重新计时）。")
        }
        return
    }

    if ($DryRun) {
        Write-Log 'INFO' ("[空跑] $Title 不在运行 —— 本该拉起它（本小时第 $($recent + 1)/$MaxRestartsPerHour 次），本次不执行。")
        return
    }

    Write-Log 'INFO' ("$Title 不在运行，拉起中（本小时第 $($recent + 1)/$MaxRestartsPerHour 次）")

    # 先记账、再启动：万一拉起的瞬间机器断电/被强杀，这一次也算已花掉的额度，
    # 反过来（先启动后记账）会让最坏情况变成"预算永远涨不上去 → 无限重启"。
    Add-LaunchRecord -State $State -Key $Key
    Save-State -State $State

    try {
        $started = & $Launch

        # 等到看见进程为止（PyInstaller 的 exe 起不来会很快退出，不必等很久）。
        $deadline = (Get-Date).AddSeconds($StartGraceSec)
        $alivePid = $null
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 800
            $check = & $Probe
            if (@($check).Count -gt 0) { $alivePid = (@($check) | ForEach-Object ProcessId) -join ', '; break }
        }

        if ($alivePid) {
            $note = '端口未探（该目标不做端口判定）'
            if ($CheckPort -gt 0) {
                if (Test-PortListening -PortNumber $CheckPort) {
                    $note = "127.0.0.1:$CheckPort 已可连接"
                } else {
                    $note = "端口 $CheckPort 暂未监听（后端冷启动还要几秒，下一次巡检会确认）"
                }
            }
            Write-Log 'INFO' ("$Title 已拉起：PID $alivePid（启动进程 PID $($started.Id)），$note")
        } else {
            Write-Log 'ERROR' ("$Title 拉起后 ${StartGraceSec}s 内没看到进程，这次算失败（预算已计一次）。" +
                               "如果反复走到这一行，多半是 exe 本身起不来，看 data\backend.log。")
        }
    } catch {
        Write-Log 'ERROR' ("$Title 拉起动作本身失败：{0}" -f $_.Exception.Message)
    }
}

# --------------------------------------------------------------------- 主流程
# 单实例锁：计划任务理论上不会重叠，但机器睡眠唤醒 / 手动跑一次都可能撞上。
# 两个看门狗同时"发现进程不在 → 各拉一次"会起出两个后端，第二个抢不到 8000 端口
# 直接崩，日志里多一条莫名其妙的错误。抢不到锁就走人，什么都别做。
$lock = New-Object System.Threading.Mutex($false, 'Local\AIAssistant-Watchdog')
$acquired = $false
try {
    $acquired = $lock.WaitOne(0, $false)
    if (-not $acquired) {
        Write-Log 'INFO' '已有另一个看门狗在跑，本次直接退出。'
        return
    }

    Write-Log 'INFO' ('---- 巡检开始{0} ----' -f $(if ($DryRun) { '（空跑模式）' } else { '' }))

    $state = Read-State

    if (-not (Test-Path -LiteralPath $BackendExe)) {
        # exe 不见的多半是 PyInstaller 正在重建（dist\run_backend 每次重建会被整体删掉）。
        # 这时候硬拉只会刷一串失败记录，所以只说清楚、不动预算。注意不 return：
        # 后端在重建不代表隧道也不用守，一起停了反而制造第二次无人知晓的停机。
        if (Test-ShouldLog -State $state -Key 'seen:no-backend-exe') {
            Write-Log 'ERROR' ("后端 exe 不存在：$BackendExe —— 若在重建 EXE，重建完自然恢复；" +
                               "若不是，说明产物被误删，需要从上一次可用版本恢复。本次不尝试拉起后端。")
        }
    } else {
        Invoke-Guard -State $state -Key 'backend' -Title '后端 run_backend.exe' -CheckPort $Port `
            -Probe { Get-BackendProcess } `
            -Launch {
                Start-Process -FilePath $BackendExe -WorkingDirectory $ProjectRoot `
                    -WindowStyle Hidden -PassThru
            }
    }

    if (-not $SkipTunnel) {
        if (Test-Path -LiteralPath $TunnelExe) {
            # 隧道挂了 = 域名对外 502/连不上，而后端可能活得好好的。只守后端的话，
            # 这种"服务在、外面进不来"的故障依旧无人知晓，所以一起盯。
            Invoke-Guard -State $state -Key 'tunnel' -Title "Cloudflare 命名隧道 $TunnelName" `
                -Probe { Get-TunnelProcess -Name $TunnelName } `
                -Launch {
                    Start-Process -FilePath $TunnelExe -ArgumentList @('tunnel', 'run', $TunnelName) `
                        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
                }
        } else {
            # tools/cloudflared.exe 不在：按节流提醒，不每分钟刷一条。
            if (Test-ShouldLog -State $state -Key 'seen:no-tunnel-exe') {
                Write-Log 'WARN' "找不到 cloudflared（$TunnelExe），本次跳过隧道守护。"
            }
        }
    }

    Save-State -State $state
    Write-Log 'INFO' '---- 巡检结束 ----'
} catch {
    Write-Log 'ERROR' ('看门狗自身出错：{0}' -f $_.Exception.Message)
} finally {
    if ($acquired) {
        $lock.ReleaseMutex()
        $lock.Dispose()
    }
}

<#
==============================================================================
为什么这个看门狗不塞进 run_backend.spec（结论：不收，也别收）
==============================================================================
1) 它守护的对象正是那份产物。PyInstaller 每次重建会整体删掉 dist\run_backend\
   ——这个坑 backend/app/core/paths.py 的注释里已经写过一次（"一次构建抹光长期
   记忆、会话和已配好的模型服务"）。守护被它守护的东西顺带删掉，等于重建完
   EXE 之后系统静默退回"只有开机自启、没有崩溃守护"的状态，而且没有任何人会
   被告知——正是本次要拆掉的那个形状。
2) 故障形状更难发现。计划任务记的是绝对路径；路径一旦随重建消失，任务每分钟
   失败一次，Event Viewer 里安静地刷错误，对外看到的还是同一段"无人知晓的停机"。
3) spec 也帮不上它。.spec 的 datas 收的是给 EXE 读的静态资源，一个 .ps1 既不会被
   Analysis 的依赖分析抓到，也不需要被抓到——它是操作系统层面用 powershell.exe
   直接解释的脚本，收进产物不产生任何功能收益。
4) 生命周期本来就该分开。看门狗属于"这台机器怎么把服务撑住"的运维配置，和
   tools\start-ai-stack.bat（现在那条 .lnk 指向的东西）是同一层：随仓库版本化、
   指向仓库里的路径、重建产物不影响它。所以它放在 deploy\，由 git 管，不进 dist\。

一句话：守护进程必须活在被守护产物的生命周期之外。
==============================================================================
#>

