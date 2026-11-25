"""S3 解析流程的錯誤。日誌只記錯誤碼，不含履歷內容。"""
from dataclasses import dataclass

MESSAGES = {
    "FILE_INVALID": "檔案不符：請上傳文字型 PDF（5MB 以內、10 頁以內、不可加密）。",
    "SCANNED_OR_EMPTY": "這份 PDF 沒有可讀取的文字（可能是掃描檔或空檔），目前不支援，請改傳文字型 PDF。",
    "LLM_ERROR": "分析服務暫時無法使用，請稍後重試。",
    "SCHEMA_INVALID": "履歷解析失敗（已自動重試一次），請稍後重試或換一份檔案。",
}


@dataclass
class ParseError(Exception):
    code: str

    @property
    def user_message(self) -> str:
        return MESSAGES[self.code]

    def __str__(self) -> str:
        return self.code
