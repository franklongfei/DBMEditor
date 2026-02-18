# DBMEditor

用于 **编辑 DBM / 向 DBM 添加文件** 的工具。

当前版本先提供一个按钮，用来直接打开 **DBMBuilder（Build）**；后续会在本项目内补齐 Editor 功能。

特点：
- DBMEditor 与 DBMBuilder 可保持完全独立（DBMEditor 通过启动外部进程调用 DBMBuilder）。
- 优先启动 DBMBuilder 的已打包 exe；若没有 exe，会尝试用 DBMBuilder 工程的 `.venv` 运行源码 `main.py`。

## 目录约定

期望和 DBMBuilder/DBMEditor 同级：

```
<root>/DBMBuilder
<root>/DBMEditor
```

## 运行

```powershell
cd <root>\DBMEditor
python .\main.py
```

## 打包

```powershell
./scripts/build.ps1 -Mode onedir
# or
./scripts/build.ps1 -Mode onefile
```
