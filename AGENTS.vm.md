# 本机环境须知（Kali 虚拟机 · DSH）

## 宿主机通道：`winhost`

这台 Kali 跑在 Windows 宿主机（`LAPTOP-3DLJGLPE`，用户 `LZY20`）的 VMware 里。
需要**在 Windows 上干活**时一律用 `winhost`，它经反向隧道访问宿主上的执行器：

```bash
winhost health                       # 先确认通道
winhost run '<PowerShell 命令>'       # 宿主执行，透传退出码
winhost sh  '<cmd.exe 命令>'
winhost ls  'C:\Users\LZY20\Desktop'
winhost put ./a.png 'C:\...\a.png'   # 也支持 get
```

- 宿主 Blender：`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`（`-b -P` 无头渲染）
- 宿主 ffmpeg：`C:\Users\LZY20\Desktop\工具\Jianying_Pro\6.0.1.11779\ffmpeg.exe`
- 中转目录：`C:\Users\LZY20\Desktop\harness\vm-bridge\work\`
- 完整说明、排障、安全边界：**`~/HOST-CHANNEL.md`**（需要宿主侧操作时先读它）

不要试图在宿主机上开监听端口或装 SSH 服务——那要管理员权限，这台机器行不通。
