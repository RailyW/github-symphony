"""PyInstaller 后端入口。"""

from symphony_github.cli import main


# 入口说明：PyInstaller 会把这个文件编译为独立可执行文件。
if __name__ == "__main__":
    raise SystemExit(main())
