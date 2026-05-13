from pathlib import Path

import pandas as pd


IDENTIFIER_TEXT_COLUMNS = ["PNU", "pnu", "법정동코드", "본번", "부번", "산여부"]


def _identifier_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


class DataLoader:
    def __init__(self, input_file):
        self.input_file = Path(input_file)

    def load(self):
        if not self.input_file.exists():
            raise FileNotFoundError(f"입력 파일을 찾지 못했습니다: {self.input_file}")
        suffix = self.input_file.suffix.lower()
        if suffix == ".csv":
            converters = {column: _identifier_text for column in IDENTIFIER_TEXT_COLUMNS}
            df = pd.read_csv(self.input_file, encoding="utf-8-sig", converters=converters)
        else:
            df = pd.read_excel(self.input_file)
        print(f"파일 로드 완료: {self.input_file.resolve()}")
        print(f"원본 데이터 크기: {df.shape}")
        return df

    @staticmethod
    def inspect_columns(df):
        print("\n[df.columns]")
        print(df.columns.tolist())
        return df.columns.tolist()
