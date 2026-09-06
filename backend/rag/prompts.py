"""RAG 相关的 prompt 模板。"""


def build_rag_prompt(base_prompt: str, context: str) -> str:
    """将检索到的上下文注入到系统 prompt 中。

    Args:
        base_prompt: 原始系统 prompt
        context: 检索到的格式化上下文

    Returns:
        拼接后的系统 prompt
    """
    if not context:
        return base_prompt

    return f"{base_prompt}\n\n{context}"
