"""独立转换已有Markdown报告，不连接数据库或调用模型。"""
import argparse
from pathlib import Path

from tools.report_renderer import render_file


def main():
    parser = argparse.ArgumentParser(description="将Markdown财报渲染为独立静态HTML")
    parser.add_argument("markdown_path", type=Path, help="Markdown报告路径")
    parser.add_argument("-o", "--output", type=Path, help="输出HTML路径，默认与源文件同名")
    args = parser.parse_args()
    print(f"HTML报告已生成：{render_file(args.markdown_path, args.output).resolve()}")


if __name__ == "__main__":
    main()
