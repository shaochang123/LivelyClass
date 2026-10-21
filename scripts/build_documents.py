"""Export the current project manual, independently of historical packages.

Supports the Markdown subset used by the manual: headings, paragraphs, lists,
fenced code and pipe tables. No model calls, uploaded PDFs or job store access.
"""

from __future__ import annotations

import argparse
import re
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs" / "产品说明书.md"
DEFAULT_OUTPUT = ROOT / "output" / "pdf" / "LivelyClass_AI教育Agent_产品说明与使用指南.pdf"
PAGE_WIDTH, PAGE_HEIGHT = A4
CONTENT_WIDTH = PAGE_WIDTH - 100


def register_font(path: Path | None = None) -> str:
    if path is not None and not path.is_file():
        raise ValueError(f"字体文件不存在：{path}")
    system_font = Path("C:/Windows/Fonts/simhei.ttf")
    selected = path or (system_font if system_font.is_file() else None)
    name = "Book2CourseCJK" if selected else "STSong-Light"
    if selected:
        pdfmetrics.registerFont(TTFont(name, str(selected)))
    else:
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
    return name


def inline(value: str) -> str:
    # Render labels instead of inserting URLs into small table cells. The
    # Markdown sources retain full links; no untrusted text becomes PDF markup.
    value = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", value)
    value = value.replace("**", "").replace("`", "")
    return escape(value)


def styles(font: str) -> dict[str, ParagraphStyle]:
    common = dict(fontName=font, wordWrap="CJK", textColor=colors.HexColor("#193344"))
    return {
        "cover": ParagraphStyle("Cover", fontSize=25, leading=39, spaceAfter=25, **common),
        "h2": ParagraphStyle("Section", fontSize=16, leading=25, spaceBefore=20,
                             spaceAfter=12, keepWithNext=True, **common),
        "h3": ParagraphStyle("Subsection", fontSize=12, leading=20, spaceBefore=13,
                             spaceAfter=8, keepWithNext=True, **common),
        "body": ParagraphStyle("Body", fontSize=10, leading=18, spaceAfter=8, **common),
        "list": ParagraphStyle("List", fontSize=10, leading=18, spaceAfter=7,
                               leftIndent=12, firstLineIndent=-12, **common),
        "code": ParagraphStyle("Code", fontSize=8.6, leading=14, spaceAfter=10,
                               leftIndent=10, rightIndent=10, borderPadding=8,
                               backColor=colors.HexColor("#EDF5F2"), **common),
        "cell": ParagraphStyle("Cell", fontSize=8.8, leading=14, **common),
        "header": ParagraphStyle("TableHeader", fontSize=8.8, leading=14,
                                 textColor=colors.white, fontName=font, wordWrap="CJK"),
        "toc": ParagraphStyle("TOC", fontSize=11, leading=22, **common),
    }


def table_rows(lines: list[str]) -> list[list[str]]:
    result = []
    for line in lines:
        cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
        cells = [cell.strip().replace(r"\|", "|") for cell in cells]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        result.append(cells)
    if not result or any(len(row) != len(result[0]) for row in result):
        raise ValueError("Markdown 表格的列数不一致。")
    return result


def markdown_blocks(text: str, theme: dict[str, ParagraphStyle]) -> list:
    story = []
    lines = text.splitlines()
    index = 0
    heading_index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("# "):
            index += 1
            continue
        if line.startswith("```"):
            index += 1
            code = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(escape(lines[index]).replace(" ", "&#160;"))
                index += 1
            if index == len(lines):
                raise ValueError("Markdown 代码块缺少结束标记。")
            story.append(Paragraph("<br/>".join(code) or "&#160;", theme["code"]))
        elif line.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            rows = table_rows(table_lines)
            cells = [[Paragraph(inline(value), theme["header" if row_no == 0 else "cell"])
                      for value in row] for row_no, row in enumerate(rows)]
            # Fixed equal widths prevent long API/filename cells from expanding
            # a table beyond the printable area; CJK paragraphs wrap inside it.
            table = LongTable(cells, colWidths=[CONTENT_WIDTH / len(rows[0])] * len(rows[0]),
                              repeatRows=1, hAlign="LEFT", spaceAfter=12)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#155D59")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F6F5")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCDCD7")),
            ]))
            story.append(table)
            continue
        elif line.startswith("## ") or line.startswith("### "):
            level = 0 if line.startswith("## ") else 1
            label = line[3 if level == 0 else 4:]
            paragraph = Paragraph(inline(label), theme["h2" if level == 0 else "h3"])
            if level == 0:
                paragraph.bookmark = f"section-{heading_index}"
                paragraph.section_title = label
                heading_index += 1
            story.append(paragraph)
        elif line.startswith("- ") or re.match(r"^\d+\. ", line):
            label = "• " + line[2:] if line.startswith("- ") else line
            story.append(Paragraph(inline(label), theme["list"]))
        else:
            paragraph = [line]
            while index + 1 < len(lines) and lines[index + 1].strip() and not re.match(
                    r"^(?:#|\||```|- |\d+\. )", lines[index + 1].strip()):
                index += 1
                paragraph.append(lines[index].strip())
            story.append(Paragraph(inline(" ".join(paragraph)), theme["body"]))
        index += 1
    return story


class GuideDocument(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if hasattr(flowable, "bookmark"):
            self.canv.bookmarkPage(flowable.bookmark)
            self.canv.addOutlineEntry(flowable.section_title, flowable.bookmark, level=0)
            self.notify("TOCEntry", (0, flowable.section_title, self.page, flowable.bookmark))


def export_pdf(source: Path, target: Path, font_path: Path | None = None) -> None:
    text = source.read_text(encoding="utf-8")
    title = next((line[2:] for line in text.splitlines() if line.startswith("# ")), source.stem)
    font = register_font(font_path)
    theme = styles(font)
    toc = TableOfContents()
    toc.levelStyles = [theme["toc"]]
    story = [Spacer(1, 100), Paragraph("LIVELY CLASS", theme["h3"]),
             Paragraph(inline(title), theme["cover"]),
             Paragraph("当前项目说明 · 使用流程 · 实现边界", theme["body"]),
             Paragraph("版本与验证结果以正文及仓库记录为准。", theme["body"]),
             PageBreak(), Paragraph("目录", theme["h2"]), toc, PageBreak()]
    story.extend(markdown_blocks(text, theme))

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#58757A"))
        canvas.drawString(50, PAGE_HEIGHT - 35, "Lively Class——AI教育Agent")
        canvas.drawRightString(PAGE_WIDTH - 50, 30, f"第 {document.page} 页")
        canvas.setStrokeColor(colors.HexColor("#CCDCD7"))
        canvas.line(50, PAGE_HEIGHT - 43, PAGE_WIDTH - 50, PAGE_HEIGHT - 43)
        canvas.restoreState()

    target.parent.mkdir(parents=True, exist_ok=True)
    document = GuideDocument(str(target), pagesize=A4, leftMargin=50, rightMargin=50,
                             topMargin=62, bottomMargin=50, title=title, author="Lively Class")
    document.multiBuild(story, onFirstPage=decorate, onLaterPages=decorate)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出当前项目说明 PDF，不调用模型。")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--font", type=Path, help="可选中文 TTF 字体；Windows 默认使用黑体。")
    args = parser.parse_args()
    export_pdf(args.source, args.output, args.font)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
