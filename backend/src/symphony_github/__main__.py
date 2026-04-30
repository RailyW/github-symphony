"""允许使用 `python -m symphony_github` 启动 CLI。"""

from .cli import main


# 入口说明：模块方式运行时，把控制权交给标准 CLI。
if __name__ == "__main__":
    main()
