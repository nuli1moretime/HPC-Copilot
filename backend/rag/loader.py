"""文档加载与切块。

读取 docs/knowledge/ 下的 markdown 文件，按 ## 标题切分为语义块，
每个块附带来源文件和标题信息，供 embedding 和检索使用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DocChunk:
    """一个文档块。"""
    id: str                    # 唯一标识，如 "107_platform_guide.md#分区与QoS"
    text: str                  # 块文本
    source_file: str           # 来源文件名
    heading: str               # 所属二级标题


def load_chunks(knowledge_dir: Path) -> list[DocChunk]:
    """加载 knowledge_dir 下所有 .md 文件，按 ## 标题切块。

    Args:
        knowledge_dir: 知识库目录路径

    Returns:
        DocChunk 列表
    """
    chunks: list[DocChunk] = []

    md_files = sorted(knowledge_dir.glob("*.md"))
    if not md_files:
        return chunks

    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        file_chunks = _split_by_headings(text, md_file.name)
        chunks.extend(file_chunks)

    return chunks


def _split_by_headings(text: str, filename: str) -> list[DocChunk]:
    """按 ## / ### 标题切分 markdown 文本。

    规则：
    - 以 ## 或 ### 开头的行作为分块边界
    - 三级标题会保留父级标题，例如“常见问题 > 更多CPU worker更快？”
    - 文件开头（第一个 ## 之前）的内容归入第一个块
    - 跳过空块和过短的块（< 20 字符）
    """
    chunks: list[DocChunk] = []

    # 按 ## 标题分割
    sections: list[tuple[str, str]] = []  # (heading, content)
    document_title = ""
    current_parent = ""
    current_heading = ""
    current_lines: list[str] = []
    saw_heading = False

    for line in text.split("\n"):
        title_match = re.match(r"^# ([^#].*)$", line)
        if title_match and not document_title:
            document_title = title_match.group(1).strip()
            continue

        heading_match = re.match(r"^(##|###) ([^#].*)$", line)
        if heading_match:
            # 保存之前的块
            if current_lines and current_heading:
                content = "\n".join(current_lines).strip()
                if len(content) >= 20:
                    sections.append((current_heading, content))
            level, title = heading_match.groups()
            saw_heading = True
            title = title.strip()
            if level == "##":
                current_parent = title
                current_heading = title
            else:
                current_heading = f"{current_parent} > {title}" if current_parent else title
            current_lines = []
        else:
            current_lines.append(line)

    # 保存最后一个块
    if current_lines:
        content = "\n".join(current_lines).strip()
        if len(content) >= 20:
            if current_heading:
                sections.append((current_heading, content))
            elif not saw_heading:
                sections.append((document_title, content))

    # 转为 DocChunk
    for i, (heading, content) in enumerate(sections):
        chunk_id = f"{filename}#{heading}" if heading else f"{filename}#section-{i}"
        chunks.append(DocChunk(
            id=chunk_id,
            text=content,
            source_file=filename,
            heading=heading,
        ))

    return chunks


def truncate_chunk(text: str, max_chars: int = 2000) -> str:
    """截断过长的块，保留开头和结尾。"""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n...\n" + text[-half:]
