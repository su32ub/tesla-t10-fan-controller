# 贡献指南

感谢参与 Tesla T10 风扇控制器项目。

## 开发环境

```bash
git clone https://github.com/<你的用户名>/tesla-t10-fan-controller.git
cd tesla-t10-fan-controller
cp config.example.json config.json

python3 -m venv .venv
. .venv/bin/activate

# Linux 桌面界面需要系统 Tk
sudo apt install python3-tk

# 网络 IPMI（可选）
pip install -r requirements-network.txt
```

## 运行

```bash
# 只读探测
python3 -m gpu_fan_controller --config config.json --probe

# 桌面程序（演练，不写硬件）
python3 -m gpu_fan_controller.desktop --config config.json --dry-run

# 网页/headless 模式
python3 -m gpu_fan_controller --config config.json --dry-run
```

## 测试

```bash
python3 -m unittest discover -v
```

提交前请确保：

- 全部单元测试通过；
- 涉及真实硬件的改动先在 `--dry-run` 下验证；
- 不提交 `config.json`、日志、`build/`、`dist/` 或任何 IPMI 密码；
- 桌面/映射/曲线相关改动附上测试或手动验证步骤。

## 代码约定

- 核心（`config.py`、`hardware.py`、`controller.py`）保持不依赖 Tkinter，确保无头模式可用；
- 硬件写入路径必须保留写后回读校验和失败保护；
- 配置写入统一走 `config.py` 的加锁原子写入；
- UI 后台任务不要阻塞 Tk 主线程，使用 `_run_async` + `root.after`；
- 新增 CLI/配置字段时保留默认值，兼容 V1 配置。

## 提交信息

推荐格式：

```
feat: add fan mapping editor
fix: release old channels when applying mappings
docs: document Windows build
```

## Pull Request

请说明：

1. 改动目的与行为变化；
2. 测试方式（单元测试、dry-run、真实硬件型号）；
3. 是否涉及安全/风扇失保逻辑。

涉及以下内容时请特别谨慎，并在 PR 中说明验证过程：

- `controller.py` 的停止/失保/写入顺序；
- `hardware.py` 的 IPMI 命令与凭据处理；
- 映射应用、`control_enabled` 切换、systemd/桌面单实例逻辑。
