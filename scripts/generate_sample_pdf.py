"""生成完全原创的参赛演示讲义；不引用受版权保护的教材。"""

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "binary_search_original.pdf"
FONT_PATH = Path("C:/Windows/Fonts/simhei.ttf")

PAGES = [
    [
        "二分查找只用于有序序列，每轮比较中间元素，把不可能的那一半候选区间排除。",
        "它保持一个不变量：若目标存在，它始终位于左边界与右边界围成的区间内。",
        "首先把左边界设为首项、右边界设为末项，再用两边界计算当前中点。",
    ],
    [
        "若中点值小于目标值，左边界移动到中点右侧；若大于目标值，右边界左移。",
        "每轮候选数量近似减半，因此比较次数随输入规模按对数增长，记作 O(log n)。",
        "找到相等值就返回索引；若左右边界交错且区间为空，就说明目标不存在。",
    ],
]


def main() -> None:
    if not FONT_PATH.is_file():
        raise SystemExit("示例 PDF 生成脚本需要本机 Windows 黑体字体。")
    pdfmetrics.registerFont(TTFont("SimHei", str(FONT_PATH)))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = canvas.Canvas(str(OUTPUT), pagesize=A4)
    document.setTitle("二分查找：绘声绘课 LivelyClass 原创演示讲义")
    document.setAuthor("绘声绘课 LivelyClass 项目组")
    width, height = A4
    for number, paragraphs in enumerate(PAGES, start=1):
        document.setFillColor(HexColor("#0E2938"))
        document.rect(0, height - 154, width, 154, stroke=0, fill=1)
        document.setFillColor(HexColor("#76DDB9"))
        document.setFont("SimHei", 12)
        document.drawString(54, height - 52, "绘声绘课 LivelyClass  /  原创演示讲义")
        document.setFillColor(HexColor("#FFFFFF"))
        document.setFont("SimHei", 25)
        document.drawString(54, height - 111, "二分查找：从范围缩小到结果判断")
        for index, text in enumerate(paragraphs, start=1):
            top = height - 214 - (index - 1) * 164
            document.setFillColor(HexColor("#E7F2EF"))
            document.roundRect(48, top - 91, width - 96, 113, 13, stroke=0, fill=1)
            document.setFillColor(HexColor("#167B70"))
            document.setFont("SimHei", 13)
            document.drawString(66, top - 10, f"知识片段 {index + (number - 1) * 3:02d}")
            document.setFillColor(HexColor("#193848"))
            document.setFont("SimHei", 10.5)
            if pdfmetrics.stringWidth(text, "SimHei", 10.5) > width - 130:
                raise ValueError("示例文本过长，不能保持一行；请缩短源文字。")
            document.drawString(66, top - 50, text)
        document.setFillColor(HexColor("#41606B"))
        document.setFont("SimHei", 10)
        document.drawString(54, 47, "本讲义为项目原创示例，可用于赛事演示与测试。")
        document.drawRightString(width - 54, 47, f"第 {number} 页 / 共 2 页")
        document.showPage()
    document.save()
    print(OUTPUT)


if __name__ == "__main__":
    main()
