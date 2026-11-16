# UX20 EDID Tool

Windows 命令行工具，用于读取、备份和写入九音九视 JYJS UX20 HDR 的 HDMI 输入 EDID。使用 Windows HID API 和 Python 标准库，无需运行原版控制器。

底层读写流程已在 USB ID 为 **048D:9337**、设备版本为 **0100** 的 UX20 HDR 上验证。设备写入 4K60 HDR 预设后，断电重连，从 USB HID 和 HDMI 侧读到的 256 字节一致。Xbox 随后实际输出了 3840×2160 @ 60 Hz，并识别到 1440p120 和 HDR10 能力。

## 使用条件

- Windows 11 或支持 PnPUtil 接口枚举功能的 Windows 版本，Python 3.9 或更新版本。
- 仅连接一张 VID 和 PID 为 048D:9337 的 UX20 HDR。
- 写入前，设备应已处于 **Internal EDID** 模式。脚本会查询模式，并在写入最后重新应用该模式。
- 准备一份适用于这条 HDMI 链路的原始 256 字节 EDID 文件，包含一个基础块和一个扩展块。仓库不附带厂商预设和设备备份。

## 刷写步骤

在脚本目录打开 PowerShell。以下示例使用 `profile.bin` 表示你准备好的 EDID。备份和导出路径必须是新文件。

1. **先读**。确认 Python 能访问采集卡，并保存一份备用副本。

   ```powershell
   python .\ux20_edid.py read .\current.bin
   ```

2. **写入**。脚本验证新旧数据的格式与校验和，先保存写前备份，再依次写入基础块和扩展块，最后应用 Internal 模式。

   ```powershell
   python .\ux20_edid.py write .\profile.bin --backup .\before-write.bin
   ```

3. **断电重新连接**。两个数据块和模式都收到确认后，拔掉采集卡的 USB 和所有 HDMI 线，等待约 10 秒。先接回 USB，等 Windows 重新识别，再接 HDMI。实测写入确认和即时读取旧 EDID 可以同时出现，完整断电后才读回新版。

4. **读回核对**。预期输出 `All 256 bytes match.`。不一致时退出码为 2。设备暂未枚举时，等 Windows 识别后再查。

   ```powershell
   python .\ux20_edid.py verify .\profile.bin
   ```

5. **验证信号源**。把 Xbox 接回 UX20 的 HDMI IN，在 Xbox 的电视和显示选项中使用「自动检测」，查看支持的模式。用下列命令读实际 HDMI 分辨率、刷新率、位深和 HDR 状态。

   ```powershell
   python .\ux20_edid.py signal
   ```

## 恢复写前 EDID

如果发现画面异常，先保留 `before-write.bin`。将它作为写入文件，并用不同路径保存恢复前的状态，再按上面的顺序断电和验证。

```powershell
python .\ux20_edid.py write .\before-write.bin --backup .\before-restore.bin
# 完全断电重连后
python .\ux20_edid.py verify .\before-write.bin
```

如果设备在某一块写入后掉线，先重连并执行 `read` 或 `verify`。写前备份保存在本地。回读以断电后的结果为准。如果当前 EDID 已损坏，脚本会停止写入，请使用厂商提供的恢复方式。

## 实现

脚本只枚举这款设备的厂商 HID 接口，依次读取或写入两个 128 字节 EDID 数据块。写入命令对两个块和模式设置分别检查设备确认。工具处理的是 EDID 配置，不包含固件刷写功能。

```powershell
python -m unittest -v
```

