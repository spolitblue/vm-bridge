# 宿主 ⇄ Kali 通道（vm-bridge）

让 **Kali 虚拟机里的 agent 能操作这台 Windows 宿主机**：执行 PowerShell/cmd 命令、
双向传文件、调用宿主上的渲染工具（Blender 等）。

```
┌──────────────────────── Windows 宿主机 ────────────────────────┐
│                                                                │
│   host_bridge.py  ── 只听 127.0.0.1:8899 ──┐                   │
│   (执行命令 / 收发文件)                     │                   │
│                                            │ ssh -N -R          │
│   run_bridge.ps1  ── 保持隧道 ──────────────┘ （宿主主动外连）    │
└────────────────────────────────────────────┼───────────────────┘
                                             │
┌──────────────────────── Kali 虚拟机 ────────┼───────────────────┐
│                                            ▼                   │
│   sshd 把宿主的 8899 反投到自己的 127.0.0.1:8899                │
│                                                                │
│   winhost  ──HTTP──> 127.0.0.1:8899 ──> 宿主上的真实执行        │
│   (Kali 侧命令行)                                               │
└────────────────────────────────────────────────────────────────┘
```

## 为什么是这个形状

这台宿主机是 **Win11 家庭版、当前账号未提权、Windows 防火墙把 VMnet8 的入站全部挡掉**
（实测 ping / 445 / 22 / 5985 从虚拟机全部不通）。所以"在宿主装 OpenSSH Server"或
"开个监听端口"都要管理员权限，还会留下防火墙口子。

反过来做就干净了：**宿主本来就能免密 SSH 进 Kali**，于是让宿主主动连过去并开一条
反向端口转发，把宿主上只绑 `127.0.0.1` 的执行器映射到 Kali 的 `127.0.0.1`。结果：

- 不需要管理员权限，不需要 UAC
- 宿主上**没有任何对外监听端口**，防火墙零改动
- 真正对外的只有 Kali 自己的 loopback，虚拟机外的机器连不到

## 文件

| 文件 | 作用 |
|---|---|
| `host_bridge.py` | 宿主端执行器，只听 `127.0.0.1:8899`，需带 token |
| `run_bridge.ps1` | 守护：拉起执行器 + 保持 SSH 反向隧道 + 换 IP 自动重连 |
| `start-bridge.vbs` | 无窗口启动 `run_bridge.ps1`（手动用；启动项里另有一份绝对路径版）|
| `token.txt` | 共享口令（首次运行自动生成；Kali 侧在同名路径 `~/.config/dsh-host-bridge/token`）|
| `bridge.log` | 守护与请求日志 |
| `ssh.log` | ssh 自身的输出（连不上时先看这里）|
| `work/` | 两边传大文件的暂存区（示例渲染产物在这里）|
| `winhost` | Kali 侧客户端源码备份（实际装在 `/usr/local/bin/winhost`）|

## 日常操作

**启动/恢复**：双击 `start-bridge.vbs`，或直接登录即可（启动项
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\dsh-host-bridge.vbs` 会自动拉起）。

**看状态**：

```powershell
Get-Content .\bridge.log -Tail 20      # 守护在干什么
Get-Content .\ssh.log -Tail 20        # 隧道为什么断
```

**停掉**：

```powershell
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
  Where-Object { $_.CommandLine -like '*-File*\run_bridge.ps1*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

**换 token**：删掉 `token.txt` 后重启守护会生成新口令，然后同步 Kali 侧
`~/.config/dsh-host-bridge/token`（权限 600）。

## Kali 侧用法

```bash
winhost health                      # 通不通
winhost run 'Get-ChildItem C:\Users\LZY20\Desktop'   # 跑 PowerShell
winhost sh  'dir'                   # 跑 cmd.exe
winhost ls  'C:\Users\LZY20'        # 列目录
winhost put ./a.mp4  'C:\Users\LZY20\Desktop\harness\vm-bridge\work\a.mp4'
winhost get 'C:\Users\LZY20\Desktop\a.mp4' ./a.mp4
winhost run --timeout 600 --cwd 'C:\work' '.\build.ps1'
```

实测：50 MB 文件往返各约 0.3 秒，内容逐字节一致。

## 实际跑通的一次渲染（验证用）

Kali 里下指令 → 宿主 Blender 无头渲染 → 结果取回：

```bash
winhost put render_check.py 'C:\Users\LZY20\Desktop\harness\vm-bridge\work\render_check.py'
winhost run --timeout 240 '& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" -b --factory-startup -P "C:\Users\LZY20\Desktop\harness\vm-bridge\work\render_check.py"'
winhost get 'C:\Users\LZY20\Desktop\harness\vm-bridge\work\render-check.png' ./render-check.png
```

结果：4.7 秒出图，PNG 320×240（`work/render-check.png`）。

宿主上可用的渲染/处理工具：

- Blender 5.1.2 — `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`
- ffmpeg — `C:\Users\LZY20\Desktop\工具\Jianying_Pro\6.0.1.11779\ffmpeg.exe`
  （另有 `Desktop\工具\blur\lib\ffmpeg\ffmpeg.exe`、`Desktop\工具\0.9.1\...\tools\ffmpeg.exe`）
- 装 Adobe 的目录里目前只有 Creative Cloud，没有 After Effects

> 注意：宿主 15.2 GB 内存，长时间渲染前先看空闲内存
> （`winhost run '"freeGB=" + [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1)'`）。

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| `winhost: cannot reach the bridge ... Connection refused` | 隧道没起来。宿主上双击 `start-bridge.vbs`，看 `ssh.log` |
| `HTTP 401` | 两边 token 不一致，见上面"换 token" |
| `remote port forwarding failed for listen port 8899` | Kali 上的旧隧道还没断干净，守护会退避重试；等 30 秒或重启 Kali 上的 sshd 会话 |
| 守护反复重连 | `ssh.log` 里通常是 Kali 没开机 / IP 变了；`run_bridge.ps1` 会用 `vmrun getGuestIPAddress` 重新找 IP |
| 宿主重启后不自动起 | 确认启动项里 `dsh-host-bridge.vbs` 还在，且路径仍指向本目录 |

## 安全边界

通道等价于 **"Kali 里的任何进程都能以 `LZY20` 身份在这台 Windows 上执行命令、读写文件"**。
守好 token 文件（两边都 600）、不要把 Kali 的 `~/.config/dsh-host-bridge/token` 和
本目录传到别的地方。不想要了就删启动项 + 杀掉守护，通道即彻底断掉。
