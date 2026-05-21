# 🚀 AI智能助手 - Windows桌面版快速打包指南

## ⚡ 一键打包流程（复制粘贴即可）

### 前置检查（一次性）

```powershell
# 1. 检查Python
python --version

# 2. 检查Flutter
flutter doctor

# 3. 启用Windows桌面支持
flutter config --enable-windows-desktop

# 4. 安装PyInstaller
pip install pyinstaller
```

### 完整打包流程

```powershell
# ===== 第1步：编译Flutter应用 =====
cd flutter_app
flutter build windows
cd ..

# ===== 第2步：打包Python后端 =====
pyinstaller --onedir --name "run_backend" `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops `
  --hidden-import uvicorn.protocols `
  --hidden-import chromadb `
  run_backend.py

# ===== 第3步：准备发布文件 =====
# 复制.env到后端目录
cp .env dist\run_backend\.env

# 创建chroma_db目录
mkdir dist\run_backend\chroma_db

# 复制Flutter输出
cp flutter_app\build\windows\x64\runner\Release\* dist\

# 复制启动脚本
cp start_ai.bat dist\

# ===== 第4步：制作安装包 =====
makensis installer.nsi

# ===== 完成！=====
echo "✅ 安装包已生成: AI智能助手_Setup.exe"
```

## 📦 测试安装包

```powershell
# 运行安装包
.\AI智能助手_Setup.exe

# 安装后，桌面会出现快捷方式
# 双击即可使用！
```

## 🔑 配置API密钥

安装后，编辑安装目录下的 `run_backend\.env` 文件：

```
DEEPSEEK_API_KEY=你的DeepSeek密钥
```

## ❓ 遇到问题？

- 查看详细文档：`docs/安装部署指南.md`
- 用户手册：`docs/用户手册.md`

---
**提示**: 首次打包可能需要较长时间（下载依赖、编译等），请耐心等待。
