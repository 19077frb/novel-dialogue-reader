"""Shared classification guidance; absence of an owner is not proof of other."""

NONPERSON_POLICY = """
先区分人物表达和非人物文本，再判断人物归属：
other=非人物文本：书名、章节标题、术语、强调词、注释、译者说明、插图标记，
以及明确只是环境音或排版符号的内容；这些不需要人物归属，不默认归给叙述者。
quotation=引用人物的原话或假想话语，仍须按当前协议判断原表达者。
人物来源不明不等于other；无法确定类型用unknown，无法确定人物按协议保留未知。
不得仅因内容短、处于括号内、只有省略号/破折号，或人物证据不足就判other。
沉默、未说出口的话、环境音和真正台词须结合上下文区分，不能批量按引号形状归类。
""".strip()
