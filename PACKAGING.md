# Windows 打包与安装说明

目标：把 USART Helper 打包成可在其他 Windows 电脑上运行的软件。目标电脑不需要安装 Python、PyQt5、numpy 或其他 Python 环境。

## 生成独立版程序

在项目目录打开 PowerShell，执行：

```powershell
.\build_windows.ps1 -Clean
```

如果当前构建电脑已经安装好所有依赖，也可以跳过依赖安装：

```powershell
.\build_windows.ps1 -Clean -SkipVenv -NoInstall
```

输出目录：

```text
dist\USART Helper\
```

把整个 `dist\USART Helper` 文件夹复制到另一台电脑，双击其中的 `USART Helper.exe` 即可运行。

## 生成安装包

如果希望像普通软件一样安装，请先在构建电脑安装 Inno Setup 6。目标电脑不需要安装 Inno Setup。

然后执行：

```powershell
.\build_windows.ps1 -Clean -Installer
```

输出安装包：

```text
installer_output\USART_Helper_Setup.exe
```

把这个安装包复制到另一台电脑运行即可。默认安装到当前用户目录，不需要管理员权限，并可创建开始菜单和桌面快捷方式。

## 构建电脑需要的环境

构建电脑需要：

- Windows
- Python 3.9 或更高版本
- 可访问 Python 包源，用于安装 `requirements.txt` 和 `requirements-build.txt`
- 可选：Inno Setup 6，用于生成安装包

目标电脑只需要：

- Windows
- 与串口设备匹配的系统驱动

## 常见输出

- 独立版：`dist\USART Helper\USART Helper.exe`
- 安装包：`installer_output\USART_Helper_Setup.exe`
- 构建缓存：`build\`、`dist\`、`.venv-build\`

构建缓存可以删除，不影响源码。
