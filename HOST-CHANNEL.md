# 宿主机通道（winhost）— Kali 虚拟机内的 agent 必读

这台 Kali 跑在 Windows 宿主机（`LAPTOP-3DLJGLPE`，用户 `LZY20`）的 VMware 里。
要在 **Windows 上做事**时用 `winhost`：跑 PowerShell / cmd、传文件、调宿主渲染工具。

## 速查

```bash
winhost health                          # 通道通不通（第一步永远先跑这个）
winhost run '<PowerShell 命令>'          # 在宿主执行，返回 stdout/stderr/退出码
winhost sh  '<cmd.exe 命令>'             # 同上，走 cmd
winhost run --timeout 600 --cwd 'C:\work' '.\build.ps1'
winhost ls  'C:\Users\LZY20\Desktop'     # 列宿主目录
winhost put ./local.png 'C:\Users\LZY20\Desktop\harness\vm-bridge\work\local.png'
winhost get 'C:\Users\LZY20\Desktop\harness\vm-bridge\work\out.mp4' ./out.mp4
```

- 退出码会透传：`winhost run` 的返回码就是远端命令的返回码。
- 大文件走 `winhost put/get`（实测 50 MB 单程约 0.3 秒），不用先打包。
- 临时中转目录：`C:\Users\LZY20\Desktop\harness\vm-bridge\work\`（两边都可读写）。

## 宿主上有什么

| 工具 | 路径 |
|---|---|
| Blender 5.1.2 | `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` |
| ffmpeg | `C:\Users\LZY20\Desktop\工具\Jianying_Pro\6.0.1.11779\ffmpeg.exe` |
| Python 3.12 | `python.exe`（已在 PATH） |
| Node | `node.exe`（已在 PATH） |

宿主规格：32 逻辑核 / 15.2 GB 内存。渲染前先看空闲内存：

```bash
winhost run '"freeGB=" + [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1)'
```

## 已验证的无头渲染流程

```bash
winhost put render.py 'C:\Users\LZY20\Desktop\harness\vm-bridge\work\render.py'
winhost run --timeout 600 '& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" -b --factory-startup -P "C:\Users\LZY20\Desktop\harness\vm-bridge\work\render.py"'
winhost get 'C:\Users\LZY20\Desktop\harness\vm-bridge\work\render-check.png' ./render-check.png
```

实测一次 320×240 Cycles 8 采样渲染耗时 4.7 秒。

## 原理（排障时有用）

宿主不监听任何对外端口。宿主上的 `run_bridge.ps1` 主动 `ssh -N -R` 连进本机的 sshd，
把宿主 `127.0.0.1:8899` 反投到本机 `127.0.0.1:8899`；`winhost` 只是带 token 访问这个
本地端口。所以：

- **不要**尝试在宿主机上开监听端口、装 OpenSSH Server 或改防火墙——那需要管理员权限，
  而且这台机器是 Win11 家庭版 + 未提权，已验证行不通。
- 通道断了就是宿主那边的守护没了，不是本机的问题。

## 排障

| 现象 | 处理 |
|---|---|
| `cannot reach the bridge ... Connection refused` | 宿主上双击 `C:\Users\LZY20\Desktop\harness\vm-bridge\start-bridge.vbs`（或让用户在宿主上执行一次）。这需要**宿主侧**动作，本机无法自愈 |
| `HTTP 401` | token 不一致：宿主 `vm-bridge\token.txt` 与 `~/.config/dsh-host-bridge/token` 需相同 |
| 连上但命令超时 | 用 `--timeout` 显式给足；长任务（渲染）给 600 以上 |
| 中文乱码 | 远端输出已按 UTF-8 解码；若 PowerShell 里再套管道，加 `| Out-String -Width 4096` |

## 能力边界与安全

- 通道等价于**以 `LZY20` 身份在宿主上执行任意命令、读写任意文件**。只在用户明确
  要求宿主侧操作时使用；别做破坏性操作（删盘、改系统设置、关杀毒），也别把
  `~/.config/dsh-host-bridge/token` 内容打印到对话里。
- 宿主上的文件就是用户真实文件。写入前确认路径，别覆盖。
