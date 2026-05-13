from __future__ import annotations

import json
import re
import sys
from pathlib import Path


FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("josa_error_convenience", re.compile(r"편의점는")),
    ("josa_error_gym", re.compile(r"헬스장는")),
    ("josa_error_hospital", re.compile(r"병원는")),
    ("josa_error_cafe", re.compile(r"카페은")),
    ("josa_error_laundry", re.compile(r"세탁소은")),
    ("generic_like_phrase", re.compile(r"처럼 확인했어요")),
    ("generic_living_summary", re.compile(r"생활 편의 조건은 .*처럼")),
    ("standalone_living_chip", re.compile(r"편의점 도보 약 \d+분\(\d+m\)")),
    ("decision_lumping", re.compile(r"예산과 통근 중 무엇을 더 우선")),
    ("choice_wording", re.compile(r"선택지입니다")),
    ("burden_not_big", re.compile(r"부담이 크지 않습니다")),
    ("transit_burden_phrase", re.compile(r"도보 부담을 함께 볼 수 있어요")),
]


def _walk_strings(value):
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_texts(texts: list[str]) -> list[dict]:
    findings: list[dict] = []
    for text in texts:
        for name, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                findings.append({"rule": name, "text": text})
                break
    return findings


def main(argv: list[str]) -> int:
    targets = [Path(arg) for arg in argv[1:]]
    if not targets:
        targets = [Path("validation_scenarios.json"), Path("presentation_validation_scenarios.json")]

    all_texts: list[str] = []
    for target in targets:
        if not target.exists():
            continue
        data = load_json(target)
        all_texts.extend(list(_walk_strings(data)))

    findings = validate_texts(all_texts)
    if findings:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 1

    print("OK: no forbidden phrases found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
