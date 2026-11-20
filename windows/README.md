# Windows 单文件发布

分发文件为 `dist/算术麻将.exe`，目标机器不需要 Python、命令行、NumPy、Torch 或 CUDA。程序带有牌桌资源、Python 运行环境、三种冻结模型及 Qt WebEngine；双击后牌桌直接显示在应用自己的窗口中。不会启动系统浏览器，也不依赖另装 WebView2。关闭游戏窗口会停止本机服务和 Bot 子进程。

当前面向 Windows 10/11 x64。程序不需要安装，不连接外部服务；加载、牌桌和退出均在同一个窗口内完成。窗口可调整大小和最大化，内嵌组件通过本机端口调用规则引擎。退出后对局和累计分数重置。

每局默认提供 30 秒开局理牌时间，可在单机设置中改为 0～600 秒（0 表示跳过）。发完每家 13 张牌后可以拖动整理，倒计时结束才开始摸打；关闭操作计时不会关闭理牌时间，下一局可保留或修改设置。

当前源码支持教学模式与逐步牌谱回放：教学可查看四家手牌并分析实际局面，暂停时冻结计时和 Bot 行动；牌谱可保存为 JSON 后导入查看。内嵌窗口通过原生保存对话框选择牌谱文件位置。本次会话保留最近 20 局，退出前请保存要保留的牌谱。源码更新不会自动重建已有 exe。

六步新手教程均已开放：前四步学习牌面、拖牌、吃碰杠与传统麻将；第五步简短展示特殊胡法；第六步演示血战先胡免赔、牌局继续与整局赔付。拖牌卡住时可求助或等待自动整理；全局“规则速查”可离线查询规则、牌池与番数。

## 在开发机重建

```powershell
.\.venv\Scripts\python.exe -m pip install -r windows/requirements-build.txt
.\.venv\Scripts\python.exe tools/build_windows.py
```

构建前检查 PySide6 QtWebEngine 组件。打包使用 `windows/ArithmeticMahjong.spec`，配置为单文件、无控制台窗口，并收集内嵌渲染进程、动态库及资源；multiprocessing 在入口调用 `freeze_support()`，防止 Bot 子进程重新打开游戏窗口。窗口默认由 Qt 使用系统图形加速并自动回退，不需要独立显卡。驱动异常的设备可在启动前设置环境变量 `MAHJONG_SOFTWARE_RENDERING=1`，强制使用软件渲染。

默认内置的三个导出：

| 类型 | 构建来源 |
| --- | --- |
| NN | `models/p4-v1/policy.npz` |
| RL | `models/rl-3h/snapshots/ppo-0068.npz` |
| CFR | `models/cfr-abstract-newrules-1h/policy.json.gz` |

可通过构建参数 `--nn-model`、`--rl-model`、`--acfr-model` 指定其他已完成导出。默认从仓库根目录 `models/` 读取。构建先复制导出并生成 SHA-256 清单，运行时使用包内 `models/` 副本，不依赖开发机缓存路径，也不跟随训练变化。训练代码和 `.data/` 不参与分发。自定义打包时也可直接收集根目录 `models/`（含 `manifest.json`）、`UI/static/` 和 `pictures/`。

构建自检使用 exe 本身检查三类真实模型、21 个策略配置、静态资源、本机接口、手牌排序、真人出牌、Bot 轮转和完整退出；另运行实际内嵌窗口，检查单窗口设置、开局、拖动排序、出牌、Bot 轮转及关闭后的进程回收，截图和 JSON 报告保存在 `build/`。开发机上通过自检不能代替所有 Windows 设备的兼容性验证。

`windows/使用说明.txt` 面向接收游戏的用户；分发压缩包只包含 exe 和这份说明。
