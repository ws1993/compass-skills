#!/usr/bin/env python3
"""Descriptive diagnostics for academic-humanizer.

The script reports reproducible candidates and counts. It does not identify an
author, assign an AI probability, or decide whether prose needs revision.
"""

import argparse
import json
import re
import statistics
import sys


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")

FENCED_CODE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
DISPLAY_MATH_RE = re.compile(
    r"\$\$.*?\$\$|\\\[.*?\\\]|\\begin\{(?:equation\*?|align\*?|gather\*?)\}.*?"
    r"\\end\{(?:equation\*?|align\*?|gather\*?)\}",
    re.S,
)
INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)[^$\n]+(?<!\$)\$(?!\$)|\\\([^\n]*?\\\)")
BLOCK_QUOTE_RE = re.compile(r"(?m)^[ \t]*>[^\n]*(?:\n|$)")
REFERENCE_HEADINGS = frozenset(
    {
        "references",
        "bibliography",
        "works cited",
        "参考文献",
        "参考文献（references）",
        "参考文献 (references)",
        "references（参考文献）",
        "references (参考文献)",
    }
)
MARKDOWN_HEADING_PREFIX_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*")
SECTION_NUMBER_PREFIX_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)|[一二三四五六七八九十百]+)"
    r"\s*(?:(?:[.)）]|[、．:：])\s*|\s+)"
)
TRAILING_HEADING_PUNCT_RE = re.compile(r"[ \t]*[:：][ \t]*$")

DOUBLE_QUOTE_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.S)
SINGLE_QUOTE_RE = re.compile(r"(?<![A-Za-z])'(?:\\.|[^'\\\n])+'(?![A-Za-z])")
CURLY_DOUBLE_QUOTE_RE = re.compile(r"“[^”]*”", re.S)
CURLY_SINGLE_QUOTE_RE = re.compile(r"‘[^’]*’", re.S)


def _blank(match):
    """Mask a span without changing offsets or line boundaries."""
    return "".join("\n" if char == "\n" else " " for char in match.group(0))


def _mask_regex(text, regex):
    return regex.sub(_blank, text)


def _normalize_reference_heading(line):
    """Return a canonical reference heading or None for ordinary prose."""
    candidate = line.strip().lstrip("\ufeff")
    candidate = MARKDOWN_HEADING_PREFIX_RE.sub("", candidate)
    candidate = SECTION_NUMBER_PREFIX_RE.sub("", candidate)
    candidate = TRAILING_HEADING_PUNCT_RE.sub("", candidate)
    candidate = " ".join(candidate.split()).casefold()
    return candidate if candidate in REFERENCE_HEADINGS else None


def _reference_section_start(text):
    """Find the last standalone, normalized reference heading."""
    offset = 0
    start = None
    for line in text.splitlines(keepends=True):
        if _normalize_reference_heading(line.rstrip("\r\n")):
            start = offset
        offset += len(line)
    return start


def mask_nonprose(text):
    """Mask code, math, block quotes, and a trailing reference section."""
    masked = text
    for regex in (
        FENCED_CODE_RE,
        INLINE_CODE_RE,
        DISPLAY_MATH_RE,
        INLINE_MATH_RE,
        BLOCK_QUOTE_RE,
    ):
        masked = _mask_regex(masked, regex)

    start = _reference_section_start(masked)
    if start is not None:
        masked = masked[:start] + "".join(
            "\n" if char == "\n" else " " for char in masked[start:]
        )
    return masked


def mask_scan_protected(text):
    """Mask nonprose and direct-quotation spans for candidate scans."""
    masked = mask_nonprose(text)
    for regex in (
        DOUBLE_QUOTE_RE,
        SINGLE_QUOTE_RE,
        CURLY_DOUBLE_QUOTE_RE,
        CURLY_SINGLE_QUOTE_RE,
    ):
        masked = _mask_regex(masked, regex)
    return masked


def route(text):
    """Return (branch, ratio, CJK tokens, Latin word tokens).

    CJK characters and Latin word runs are orthographic tokens. Treating an
    embedded technical term as one Latin token prevents a few long model names
    from overriding the grammar of a Chinese sentence.
    """
    prose = mask_nonprose(text)
    cjk = len(CJK_RE.findall(prose))
    latin = len(LATIN_WORD_RE.findall(prose))
    total = cjk + latin
    if total == 0:
        return "unknown", None, cjk, latin
    ratio = round(cjk / total, 4)
    return ("zh" if ratio >= 0.5 else "en"), ratio, cjk, latin


EN_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")
ZH_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；])")


def sentences(text, branch):
    prose = re.sub(r"\s*\n\s*", " ", mask_nonprose(text)).strip()
    if not prose:
        return []
    splitter = ZH_SENT_SPLIT_RE if branch == "zh" else EN_SENT_SPLIT_RE
    return [part.strip() for part in splitter.split(prose) if part.strip()]


def sentence_length(sentence, branch):
    if branch == "zh":
        return len(CJK_RE.findall(sentence)) + len(LATIN_WORD_RE.findall(sentence))
    return len(LATIN_WORD_RE.findall(sentence))


def lexical_diversity(text, branch):
    prose = mask_nonprose(text)
    if branch == "zh":
        tokens = CJK_RE.findall(prose)
        measure = "character_diversity"
        unit = "cjk_characters"
    elif branch == "en":
        tokens = [word.lower() for word in LATIN_WORD_RE.findall(prose)]
        measure = "word_ttr"
        unit = "latin_words"
    else:
        tokens = []
        measure = "unavailable"
        unit = "none"
    value = round(len(set(tokens)) / len(tokens), 4) if tokens else None
    return {
        "measure": measure,
        "value": value,
        "tokens": len(tokens),
        "unit": unit,
        "limitation": "length_sensitive_description_only",
    }


def sentence_stats(text, branch):
    if branch not in {"en", "zh"}:
        return {"n": 0, "mean": None, "stdev": None, "min": None, "max": None}
    lengths = [
        sentence_length(sentence, branch)
        for sentence in sentences(text, branch)
        if sentence_length(sentence, branch) > 0
    ]
    if not lengths:
        return {"n": 0, "mean": None, "stdev": None, "min": None, "max": None}
    return {
        "n": len(lengths),
        "mean": round(statistics.mean(lengths), 2),
        "stdev": round(statistics.stdev(lengths), 2) if len(lengths) > 1 else 0.0,
        "min": min(lengths),
        "max": max(lengths),
    }


EN_TRANSITIONS = (
    "moreover",
    "furthermore",
    "additionally",
    "in addition",
    "however",
    "therefore",
    "thus",
    "hence",
    "nevertheless",
    "nonetheless",
    "consequently",
    "accordingly",
    "specifically",
    "based on these results",
    "taken together",
)
ZH_TRANSITIONS = (
    "然而",
    "相比之下",
    "此外",
    "因此",
    "据此",
    "与此同时",
    "具体而言",
    "基于上述结果",
    "综上",
)


def transition_density(text, branch):
    spans = sentences(text, branch)
    if not spans:
        return 0.0, {}
    vocabulary = ZH_TRANSITIONS if branch == "zh" else EN_TRANSITIONS
    counts = {}
    for sentence in spans:
        head = sentence.lstrip('"“\'(').lower() if branch == "en" else sentence.lstrip('“"（(')
        for marker in vocabulary:
            if head.startswith(marker):
                counts[marker] = counts.get(marker, 0) + 1
                break
    hits = sum(counts.values())
    return round(100.0 * hits / len(spans), 1), counts


PASSIVE_PARTICIPLES = (
    "analyzed",
    "analysed",
    "applied",
    "assigned",
    "built",
    "calculated",
    "collected",
    "computed",
    "conducted",
    "defined",
    "derived",
    "drawn",
    "estimated",
    "evaluated",
    "excluded",
    "followed",
    "found",
    "given",
    "implemented",
    "included",
    "incubated",
    "measured",
    "observed",
    "obtained",
    "performed",
    "randomized",
    "randomised",
    "recorded",
    "reported",
    "selected",
    "shown",
    "tested",
    "trained",
    "used",
)
PASSIVE_CANDIDATE_RE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(?:"
    + "|".join(PASSIVE_PARTICIPLES)
    + r")\b",
    re.I,
)


def passive_candidates_en(text):
    """Return conservative candidates; absence is not evidence of active voice."""
    return [
        {"match": match.group(0), "sentence": sentence}
        for sentence in sentences(text, "en")
        for match in [PASSIVE_CANDIDATE_RE.search(sentence)]
        if match
    ]


def dash_counts(text):
    fullwidth_double = len(re.findall(r"(?<!—)——(?!—)", text))
    without_pairs = re.sub(r"(?<!—)——(?!—)", "  ", text)
    return {
        "em_dash_single": without_pairs.count("—"),
        "fullwidth_double": fullwidth_double,
        "double_hyphen": len(re.findall(r"(?<!-)--(?!-)", text)),
        "en_dash_spaced": len(re.findall(r"\s–\s", text)),
    }


ZH_PROCESS_TARGET = (
    r"(?:本文|本稿|本节|本段|此段|该段|本部分|此部分|该部分|"
    r"以下|下文|这里|此处|稿件|文本|版本|措辞|句式|表达|"
    r"控制|压缩|改写|修改|调整|采用|使用|删除|保留|限制|润色)"
)
ZH_REQUEST_LEAD = r"(?:按|按照|依照|根据)"
ZH_REQUEST_ADDRESSEE = r"(?:您|你|用户)(?:的)?"
ZH_REQUESTED_PROCESS_RE = re.compile(
    r"(?:"
    + ZH_REQUEST_LEAD
    + ZH_REQUEST_ADDRESSEE
    + r"(?:要求|指示)"
    r"|"
    + ZH_REQUEST_LEAD
    + r"(?:要求|指示)[，,:：]?\s*[^。！？\n]{0,12}"
    + ZH_PROCESS_TARGET
    + r")"
)
ZH_DOCUMENT_SUBJECT = (
    r"(?:本文|本稿|本节|本段|此段|该段|文本|稿件|内容|表达|措辞|句式)"
)
ZH_AI_STYLE_SELF_REFERENCE_RE = re.compile(
    r"(?:"
    r"(?:为了|为|以)\s*(?:(?:避免|防止)\s*)?(?:不\s*)?"
    r"(?:(?:让|使)\s*)?"
    + ZH_DOCUMENT_SUBJECT
    + r"?\s*(?:显得|看起来|读起来|听起来)\s*像\s*AI(?:\s*生成)?"
    r"|(?:为了|为|以)\s*不\s*像\s*AI(?:\s*生成)?"
    r"|(?:为了|为|以)?\s*(?:避免|防止|消除|去除)\s*"
    r"(?:使用\s*)?AI\s*(?:味|腔|痕迹|写作痕迹|生成痕迹)"
    r")",
    re.I,
)
ZH_DISCUSSION_PREFIX_RE = re.compile(
    r"(?:"
    r"(?:研究|分析|评估|调查|讨论|考察|测试|检测)(?:了|中|表明|发现)?"
    r"|(?:提示词?|指令|问卷|语料|样本)(?:要求|包含|使用|出现)?"
    r"|模型(?:被)?要求"
    r")[^。！？\n]{0,18}$"
)


LEAK_PATTERNS = (
    (
        "en.requested_process",
        re.compile(
            r"\bAs (?:you )?(?:requested|instructed)(?!\s+by\b)"
            r"(?=\s*(?:[:,]|\bthis\b|\bthe\b|\bwe\b|\bI\b))",
            re.I,
        ),
    ),
    (
        "en.constraint_restatement",
        re.compile(
            r"\bPer (?:your|the) [\w -]{0,40}(?:constraint|requirement|word (?:limit|count))\b",
            re.I,
        ),
    ),
    (
        "en.ai_style_self_reference",
        re.compile(
            r"\bTo avoid (?:AI[- ]sounding|sounding like AI)(?:\s+(?:language|phrasing|prose))?"
            r"\s*,?\s*(?:this|the)\s+(?:paper|section|text|draft|manuscript)\b",
            re.I,
        ),
    ),
    (
        "en.editor_preface",
        re.compile(r"\bHere is the (?:revised|rewritten|humanized|updated) (?:version|text|draft)\b", re.I),
    ),
    (
        "en.assistant_identity",
        re.compile(r"\bAs an (?:AI|editor|assistant|language model)\b", re.I),
    ),
    (
        "zh.requested_process",
        ZH_REQUESTED_PROCESS_RE,
    ),
    (
        "zh.ai_style_self_reference",
        ZH_AI_STYLE_SELF_REFERENCE_RE,
    ),
    (
        "zh.editor_preface",
        re.compile(r"以下是(?:修改|改写|去\s*AI\s*味|润色)后的(?:版本|文本|内容)"),
    ),
    ("tool.tracking", re.compile(r"(?:utm_source=(?:chatgpt\.com|claude\.ai|openai|perplexity\.ai)|referrer=grok\.com)", re.I)),
    ("tool.citation_token", re.compile(r"citeturn\d|oaicite|\[attached_file:\d+\]", re.I)),
    ("placeholder.english", re.compile(r"\[(?:Your|INSERT|Add|Enter|Describe|Specify)[^\]\n]{0,60}\]", re.I)),
    ("placeholder.date", re.compile(r"\b\d{4}-XX(?:-XX)?\b", re.I)),
    ("placeholder.chinese", re.compile(r"XX(?:大学|学院|医院|公司|路|省|市)|[【\[](?:填写|待补充|待定)[】\]]")),
)


def _context(text, start, end, radius=45):
    return text[max(0, start - radius):min(len(text), end + radius)].replace("\n", " ")


def _is_discussed_ai_style_example(rule, text, start):
    if rule != "zh.ai_style_self_reference":
        return False
    prefix = text[max(0, start - 40):start]
    return bool(ZH_DISCUSSION_PREFIX_RE.search(prefix))


def leak_scan(text):
    masked = mask_scan_protected(text)
    hits = []
    for rule, regex in LEAK_PATTERNS:
        for match in regex.finditer(masked):
            if _is_discussed_ai_style_example(rule, masked, match.start()):
                continue
            hits.append(
                {
                    "rule": rule,
                    "status": "candidate_only",
                    "match": text[match.start():match.end()],
                    "context": _context(text, match.start(), match.end()),
                    "status": "candidate_only",
                }
            )
    return sorted(hits, key=lambda item: text.find(item["match"]))


EN_CONTRAST_PATTERNS = (
    (
        "en.more_than_escalation",
        re.compile(
            r"\b(?:is|are|was|were)\s+more\s+than\s+"
            r"(?:(?:just|merely|simply)\s+)?(?:an?|the)\b[^.!?]{1,100}?"
            r"(?:;|,|—|--)\s*(?:it|this|they|the\s+\w+)\s+"
            r"(?:is|are|was|were)\b[^.!?]+",
            re.I,
        ),
    ),
    (
        "en.split_not",
        re.compile(
            r"\b(?:this|it|the\s+\w+)\s+(?:is|are|was|were)\s+not\s+"
            r"[^.!?]{1,100}[.!?]\s+(?:it|this|the\s+\w+)\s+"
            r"(?:is|are|was|were)\s+[^.!?]+",
            re.I,
        ),
    ),
    (
        "en.not_merely",
        re.compile(
            r"\bnot\s+(?:merely|just|simply)\b[^.!?]{1,120}?(?:;|,|—|--)"
            r"\s*(?:(?:but\s+)?(?:it\s+)?(?:is|are)\s+)?[^.!?]+",
            re.I,
        ),
    ),
    (
        "en.not_only",
        re.compile(
            r"\bnot\s+only\b[^.!?]{1,120}?"
            r"(?:\bbut\s+also\b|[,;]\s*(?:it|they|this|the\s+\w+)\s+also\b)"
            r"[^.!?]+",
            re.I,
        ),
    ),
    ("en.not_but", re.compile(r"\bnot\b[^.!?]{1,100}?\bbut\b[^.!?]+", re.I)),
    ("en.less_more", re.compile(r"\bless\s+about\b[^.!?]{1,100}?\bmore\s+about\b[^.!?]+", re.I)),
    ("en.rather_than", re.compile(r"\brather\s+than\b[^.!?]+", re.I)),
)
ZH_CONTRAST_PATTERNS = (
    (
        "zh.split_not",
        re.compile(
            r"(?:这|它)?不是[^。！？；]{1,60}[。！？]\s*"
            r"(?:这|它)?(?:是|意味着|构成)[^。！？；]+"
        ),
    ),
    ("zh.not_only", re.compile(r"(?:不仅仅?是|不只是|不单是|不仅)[^。！？；]{1,80}?(?:，|,)?(?:更是|而且|还|也)[^。！？；]*")),
    ("zh.not_but", re.compile(r"(?:不是|并非)[^。！？；]{1,80}?(?:，|,)?而是[^。！？；]*")),
    ("zh.not_where", re.compile(r"不在于[^。！？；]{1,80}?(?:，|,)?而在于[^。！？；]*")),
    ("zh.rather", re.compile(r"与其说[^。！？；]{1,80}?(?:，|,)?不如说[^。！？；]*")),
    (
        "zh.surface_essence",
        re.compile(
            r"表面(?:上)?[^。！？；]{1,80}?(?:，|,|；|;)?"
            r"(?:本质|实质)(?:上)?[^。！？；]*"
        ),
    ),
)


def contrast_candidates(text, branch):
    """Return surface candidates; semantic validity requires contrast-logic.md."""
    if branch not in {"en", "zh"}:
        return []
    masked = mask_scan_protected(text)
    patterns = ZH_CONTRAST_PATTERNS if branch == "zh" else EN_CONTRAST_PATTERNS
    raw = []
    for rule, regex in patterns:
        for match in regex.finditer(masked):
            raw.append((match.start(), match.end(), rule))

    # Prefer the widest match and collapse overlapping pattern families.
    raw.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected = []
    for start, end, rule in raw:
        if any(start < existing_end and end > existing_start for existing_start, existing_end, _ in selected):
            continue
        selected.append((start, end, rule))

    return [
        {
            "rule": rule,
            "match": text[start:end],
            "context": _context(text, start, end),
            "status": "candidate_only",
        }
        for start, end, rule in sorted(selected)
    ]


def analyze(text):
    branch, ratio, cjk, latin = route(text)
    transition_value, transition_counts = transition_density(text, branch)
    return {
        "route": {
            "branch": branch,
            "cjk_ratio": ratio,
            "cjk_tokens": cjk,
            "latin_word_tokens": latin,
            "unit": "orthographic_tokens",
        },
        "lexical_diversity": lexical_diversity(text, branch),
        "sentence_length": sentence_stats(text, branch),
        "transitions": {
            "value": transition_value,
            "unit": "per_100_sentences",
            "by_marker": transition_counts,
        },
        "passive_candidates": passive_candidates_en(text) if branch == "en" else [],
        "dashes": dash_counts(text),
        "leaks": leak_scan(text),
        "contrast_candidates": contrast_candidates(text, branch),
        "interpretation": "descriptive_candidates_not_authorship_or_quality_verdict",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Descriptive diagnostics for academic-humanizer"
    )
    parser.add_argument("file", help="input file, or - for stdin")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--route", action="store_true", help="print language route only")
    args = parser.parse_args()

    if args.file == "-":
        text = sys.stdin.read()
    else:
        with open(args.file, encoding="utf-8") as handle:
            text = handle.read()

    if args.route:
        branch, ratio, cjk, latin = route(text)
        print(
            f"branch={branch} cjk_ratio={ratio} "
            f"cjk_tokens={cjk} latin_word_tokens={latin}"
        )
        return

    report = analyze(text)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    route_report = report["route"]
    print(
        f"[route] branch={route_report['branch']} "
        f"r={route_report['cjk_ratio']} "
        f"cjk={route_report['cjk_tokens']} "
        f"latin_words={route_report['latin_word_tokens']}"
    )
    diversity = report["lexical_diversity"]
    print(
        f"[lexical] measure={diversity['measure']} value={diversity['value']} "
        f"tokens={diversity['tokens']}"
    )
    length = report["sentence_length"]
    print(
        f"[sentences] n={length['n']} mean={length['mean']} "
        f"stdev={length['stdev']} range={length['min']}-{length['max']}"
    )
    transitions = report["transitions"]
    print(
        f"[transitions] {transitions['value']} per 100 sentences "
        f"{transitions['by_marker'] or ''}"
    )
    print(f"[passive candidates] {len(report['passive_candidates'])}")
    print(f"[dashes] {report['dashes']}")
    print(f"[leak candidates] {len(report['leaks'])}")
    print(f"[contrast candidates] {len(report['contrast_candidates'])}")


if __name__ == "__main__":
    main()
