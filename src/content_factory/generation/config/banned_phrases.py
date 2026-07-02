"""Запрещённые фразы и паттерны для линтинга."""

BANNED_BY_LANG = {
    "ru": [
        r"\bнажм(и|ите)\b",
        r"\bкликн(и|ите)\b",
        r"\bперейд(и|ите)\b",
        r"\bввед(и|ите)\b",
        r"\bскач(ай|айте)\b",
        r"\bоткрой(те)?\b",
        r"\bвыбери(те)?\b",
        r"\bзапусти(те)?\b",
        r"\bскопируй(те)?\b",
    ],
    "en": [
        r"\bclick\b",
        r"\bpress\b",
        r"\bgo to\b",
        r"\benter\b",
        r"\bcopy\b",
        r"\bdownload\b",
        r"\bopen\b",
        r"\bselect\b",
        r"\brun\b",
    ],
    "ky": [
        r"\bclick\b",
        r"\bpress\b",
        r"\bgo to\b",
        r"\benter\b",
        r"\bopen\b",
        r"\bselect\b",
        r"\brun\b",
        r"\bdownload\b",
    ],
}

BAD_GOAL_PATTERNS = {
    "ru": [
        r"\bизуч(ить|и)(\s+как\s+цель)?\b",
        r"\bознаком(иться|ься)(\s+как\s+цель)?\b",
        r"\bпосмотр(еть|и)(\s+как\s+цель)?\b",
    ],
    "en": [r"\bstudy\b", r"\bfamiliarize\b", r"\blook at\b"],
    "ky": [],
}

