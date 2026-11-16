# 游玩模型

本目录只存放已经完成训练、可直接推理的冻结权重。下载源码后不需要重新训练，也不需要读取 `.data/`。

| 文件 | 用途 |
| --- | --- |
| `p4-v1/policy.npz` | 默认 NN 模型 |
| `rl-3h/snapshots/ppo-0068.npz` | 桌面版使用的 RL 第 68 批模型，命令行可通过 `--model` 指定 |
| `cfr-abstract-newrules-1h/policy.json.gz` | 桌面版使用的新规则 CFR 模型，命令行可通过 `--acfr-model` 指定 |
| `rl-v1/policy.npz` | 保留通用 Bot 工厂原有的 RL 默认模型 |
| `cfr-abstract-v1/policy.json.gz` | 保留命令行和通用 CFR Bot 原有的默认模型 |

`manifest.json` 记录桌面版选用的三份权重的相对路径和 SHA-256。源码桌面入口和打包后的程序都从资源根目录的 `models/` 加载；自定义打包时需要包含此目录、`UI/static/` 和 `pictures/`。现有 Windows 构建脚本会根据所选权重生成同样的资源结构。

模型格式保持不变：NN/RL 使用 NumPy 的 `.npz`，CFR 使用 `.json.gz`。训练数据、`resume` 检查点、独立训练码本及其他批次保留在本地 `.data/`，不放入本目录。
