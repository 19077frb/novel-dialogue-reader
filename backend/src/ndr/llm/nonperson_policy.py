"""Shared classification guidance; absence of an owner is not proof of other."""

NONPERSON_POLICY_VERSION = "nonperson-2"
NONPERSON_POLICY_LEGACY = """
先区分人物表达和非人物文本，再判断人物归属：
other=非人物文本：书名、章节标题、术语、强调词、注释、译者说明、插图标记，
以及明确只是环境音或排版符号的内容；这些不需要人物归属，不默认归给叙述者。
quotation=引用人物的原话或假想话语，仍须按当前协议判断原表达者。
人物来源不明不等于other；无法确定类型用unknown，无法确定人物按协议保留未知。
不得仅因内容短、处于括号内、只有省略号/破折号，或人物证据不足就判other。
沉默、未说出口的话、环境音和真正台词须结合上下文区分，不能批量按引号形状归类。
""".strip()

NONPERSON_POLICY = """
先区分人物表达和非人物文本，再判断人物归属：
other=非人物文本：书名、章节标题、术语、强调词、注释、译者说明、插图标记，
以及明确只是环境音或排版符号的内容；这些不需要人物归属，不默认归给叙述者。
引号不代表对白：括号中的修饰（如美人）、修辞说明（如夸饰法）、术语解释、
叙述中特意强调的词语均判other。先读引号前后原文，不因扫描器将其列为目标就分配人物。
泛指社会观念、宣传用语、假设任意路人会说的话、并非具体人物的虚构台词模板判other；
它们没有需要识别的具体表达者。实际人物的引用、回忆或针对具体人物设想的话判quotation，
仍须按当前协议判断原表达者；不能仅因来源不明就改other。
人物来源不明不等于other；无法确定类型用unknown，无法确定人物按协议保留未知。
不得仅因内容短、处于括号内、只有省略号/破折号，或人物证据不足就判other。
沉默、未说出口的话、环境音和真正台词须结合上下文区分，不能批量按引号形状归类。
上下文明说无言对看、沉默且没有人物表达时可判other；有明确思考者的心声仍判thought。
""".strip()


def nonperson_policy(version):
    if version is None:
        return NONPERSON_POLICY_LEGACY
    if version != NONPERSON_POLICY_VERSION:
        raise ValueError("不支持的非人物分类版本")
    return NONPERSON_POLICY
