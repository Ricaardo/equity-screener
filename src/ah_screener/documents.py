from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

import pandas as pd


DOCUMENT_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI算力硬件": ("人工智能", "AI", "算力", "数据中心", "服务器", "GPU", "云计算", "光模块"),
    "半导体国产替代": ("半导体", "芯片", "集成电路", "晶圆", "封装", "光刻", "EDA"),
    "人形机器人与高端制造": ("机器人", "自动化", "工业母机", "伺服", "减速器", "传感器"),
    "创新药与医疗科技": ("创新药", "临床", "药物", "医疗器械", "研发管线", "BD", "license-out"),
    "高股息央国企防御": ("股息", "分红", "派息", "现金分红", "央企", "国企", "公用事业"),
    "电力储能与能源转型": ("储能", "光伏", "风电", "新能源", "电网", "电池", "充电"),
    "资源涨价与安全资产": ("黄金", "铜", "铝", "煤炭", "石油", "天然气", "稀土", "矿产"),
    "港股AI互联网平台": ("平台", "用户", "广告", "游戏", "云服务", "电商", "本地生活", "互联网"),
    "汽车智能化与出海": ("智能驾驶", "电动车", "新能源汽车", "整车", "零部件", "出口", "海外"),
}

EXTRACTION_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "business_structure": ("业务结构", ("主营业务", "业务分部", "分部收入", "segment", "revenue by")),
    "rd_investment": ("研发投入", ("研发开支", "研发费用", "研发投入", "research and development", "R&D")),
    "customer_concentration": ("客户集中度", ("主要客户", "五大客户", "最大客户", "customer concentration")),
    "audit_opinion": ("审计意见", ("审计意见", "无保留意见", "保留意见", "qualified opinion", "audit opinion")),
    "risk_factor": ("风险提示", ("风险因素", "主要风险", "risk factor", "principal risks")),
}

DOCUMENT_RISK_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "frequent_corporate_action": (
        "频繁合股/供股/配股",
        ("合股", "股份合并", "供股", "公开发售", "配股", "股本重组", "share consolidation", "rights issue", "open offer"),
    ),
    "delayed_reporting": (
        "延迟刊发财报/业绩",
        ("延迟刊发", "延迟发表", "延期刊发", "未能刊发", "delay in publication", "delayed publication"),
    ),
    "abnormal_audit_opinion": (
        "异常审计意见",
        ("保留意见", "无法表示意见", "否定意见", "qualified opinion", "disclaimer of opinion", "adverse opinion"),
    ),
}


def _normalize_symbol(market: str, symbol: str) -> str:
    if market == "A":
        return symbol.zfill(6)
    if market == "HK":
        return symbol.zfill(5)
    return symbol.upper()


def extract_pdf_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")

    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError as exc:
        raise RuntimeError("Install pdfplumber or pypdf to parse PDF documents.") from exc


def _sentences(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[。！？.!?])\s*", compact)
    if len(parts) <= 1:
        parts = [compact[i : i + 280] for i in range(0, len(compact), 280)]
    return [part.strip() for part in parts if part.strip()]


def _find_evidence(text: str, keywords: tuple[str, ...], limit: int = 2) -> list[str]:
    lowered_keywords = [keyword.lower() for keyword in keywords]
    matches: list[str] = []
    for sentence in _sentences(text):
        lower = sentence.lower()
        if any(keyword in lower for keyword in lowered_keywords):
            matches.append(sentence[:360])
        if len(matches) >= limit:
            break
    return matches


def _document_id(market: str, symbol: str, path: Path, file_sha256: str) -> str:
    payload = f"{market}:{symbol}:{path.name}:{file_sha256}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def build_document_records(
    *,
    market: str,
    symbol: str,
    path: Path,
    document_type: str = "annual_report",
    report_date: str | None = None,
    title: str | None = None,
    source_url: str | None = None,
    source: str = "official_pdf",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    path = path.expanduser().resolve()
    text = extract_pdf_text(path)
    file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    normalized_market = market.upper()
    normalized_symbol = _normalize_symbol(normalized_market, symbol)
    document_id = _document_id(normalized_market, normalized_symbol, path, file_sha256)
    updated_at = pd.Timestamp(datetime.now())
    parsed_report_date = pd.to_datetime(report_date, errors="coerce") if report_date else pd.NaT

    document = pd.DataFrame(
        [
            {
                "document_id": document_id,
                "market": normalized_market,
                "symbol": normalized_symbol,
                "document_type": document_type,
                "report_date": parsed_report_date,
                "title": title or path.stem,
                "source_url": source_url,
                "local_path": str(path),
                "file_sha256": file_sha256,
                "source": source,
                "updated_at": updated_at,
            }
        ]
    )

    extraction_rows: list[dict[str, object]] = []
    for extract_type, (label, keywords) in EXTRACTION_RULES.items():
        evidence = _find_evidence(text, keywords)
        if not evidence:
            continue
        extraction_rows.append(
            {
                "document_id": document_id,
                "market": normalized_market,
                "symbol": normalized_symbol,
                "extract_type": extract_type,
                "extract_key": label,
                "extract_value": "；".join(evidence),
                "evidence_text": evidence[0],
                "evidence_level": "B",
                "source": source,
                "updated_at": updated_at,
            }
        )

    tag_rows: list[dict[str, object]] = []
    for theme, keywords in DOCUMENT_THEME_KEYWORDS.items():
        evidence = _find_evidence(text, keywords, limit=1)
        if not evidence:
            continue
        tag_rows.append(
            {
                "market": normalized_market,
                "symbol": normalized_symbol,
                "tag_type": "theme",
                "tag_name": theme,
                "evidence_level": "B",
                "source": f"{source}:{path.name}",
                "updated_at": updated_at,
            }
        )
        extraction_rows.append(
            {
                "document_id": document_id,
                "market": normalized_market,
                "symbol": normalized_symbol,
                "extract_type": "theme_tag",
                "extract_key": theme,
                "extract_value": theme,
                "evidence_text": evidence[0],
                "evidence_level": "B",
                "source": source,
                "updated_at": updated_at,
            }
        )

    for risk_key, (label, keywords) in DOCUMENT_RISK_RULES.items():
        evidence = _find_evidence(text, keywords, limit=1)
        if not evidence:
            continue
        tag_rows.append(
            {
                "market": normalized_market,
                "symbol": normalized_symbol,
                "tag_type": "risk",
                "tag_name": label,
                "evidence_level": "B",
                "source": f"{source}:{path.name}",
                "updated_at": updated_at,
            }
        )
        extraction_rows.append(
            {
                "document_id": document_id,
                "market": normalized_market,
                "symbol": normalized_symbol,
                "extract_type": "risk_signal",
                "extract_key": risk_key,
                "extract_value": label,
                "evidence_text": evidence[0],
                "evidence_level": "B",
                "source": source,
                "updated_at": updated_at,
            }
        )

    extractions = pd.DataFrame(extraction_rows)
    tags = pd.DataFrame(tag_rows)
    return document, extractions, tags
