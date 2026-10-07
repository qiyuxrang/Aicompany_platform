"""Deterministic checks shared by report generation and final-file inspection."""

import re


FINANCIAL_CONCLUSION = re.compile(
    r"(?:总投资|投资(?:成本|金额|总额|额|回收期)|建设投资|回报率|收益率|内部收益率|净现值|"
    r"(?:预计|年均|年度|项目)(?:收益|利润|回报))[^。；\n]{0,24}?\d+(?:\.\d+)?\s*(?:%|％|万|亿|元|年|个月)?"
)


def has_repeated_filler(paragraphs):
    seen = set()
    repeated_characters = 0
    repeated_prefixes = {}
    for paragraph in paragraphs:
        normalized = re.sub(r"\s+", "", paragraph)
        if len(normalized) >= 500:
            prefix = normalized[:200]
            repeated_prefixes[prefix] = repeated_prefixes.get(prefix, 0) + 1
            if repeated_prefixes[prefix] >= 4:
                return True
        if len(normalized) < 1000:
            continue
        if normalized in seen:
            repeated_characters += len(normalized)
            if repeated_characters >= 2000:
                return True
        seen.add(normalized)
        prefix = [0] * len(normalized)
        for index in range(1, len(normalized)):
            length = prefix[index - 1]
            while length and normalized[index] != normalized[length]:
                length = prefix[length - 1]
            if normalized[index] == normalized[length]:
                length += 1
            prefix[index] = length
        period = len(normalized) - prefix[-1]
        if period <= len(normalized) // 4 and len(normalized) % period == 0:
            return True
    return False


def unsupported_financial_conclusion(paragraph):
    return bool(FINANCIAL_CONCLUSION.search(paragraph))
