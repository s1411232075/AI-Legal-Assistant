from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import textwrap
import urllib.error
import urllib.request
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from lxml import etree


ROOT = Path(__file__).resolve().parent
DEFAULT_SAMPLE = ROOT / "存證信函範例.docx"
DEFAULT_TEMPLATE = ROOT / "存證信函空白.docx"
DEFAULT_JSON = ROOT / "notice_data.json"
DEFAULT_MEMORY = ROOT / "rag_memory.json"
DEFAULT_OUTPUT = ROOT / "存證信函_完成.docx"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:1.5b"
HTTP_TIMEOUT_SECONDS = 15

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W_T = f"{{{NS['w']}}}t"
ROW_MARKERS = list("一二三四五六七八九十")
ROWS_PER_COPY = 10
CHARS_PER_ROW = 20
BODY_CAPACITY = ROWS_PER_COPY * CHARS_PER_ROW


FIELD_PROMPTS = [
    ("sender_name", "寄件人姓名"),
    ("sender_address", "寄件人詳細地址"),
    ("recipient_name", "收件人姓名"),
    ("recipient_address", "收件人詳細地址"),
    ("event_date", "事件日期，例如：民國112年5月1日"),
    ("case_summary", "事件簡述，例如：對方借車後逾期未歸還"),
    ("request", "希望對方履行的事項，例如：返還車輛並賠償新臺幣參萬元整"),
    ("deadline", "履行期限，例如：函到七日內"),
    ("legal_basis", "法律依據或主張，例如：民法侵權行為及侵占；不知道可直接按 Enter"),
    ("consequence", "逾期未處理的後續措施，例如：將依法提起訴訟"),
]

DOMAIN_KEYWORDS = [
    "存證信函",
    "寄件人",
    "收件人",
    "欠款",
    "借款",
    "還款",
    "金額",
    "期限",
    "函到",
    "逾期",
    "證據",
    "起訴",
    "訴訟",
    "法律",
    "民法",
    "內容",
    "正文",
    "格式",
    "郵局",
    "寄送",
    "對方",
    "台端",
    "本人",
    "這件",
    "案件",
    "修改",
    "重寫",
    "補充",
]

UNRELATED_HINTS = [
    "天氣",
    "股票",
    "遊戲",
    "食譜",
    "旅遊",
    "電影",
    "程式作業",
    "英文翻譯",
    "數學",
    "歷史",
]

LAW_ARTICLES = {
    "civil_474": {
        "law_name": "民法",
        "article": "第474條",
        "title": "消費借貸",
        "url": "https://mojlaw.moj.gov.tw/LawContentExtent.aspx?LSID=FL001351&LawNo=474&media=print",
        "fallback_text": "稱消費借貸者，謂當事人一方移轉金錢或其他代替物之所有權於他方，而約定他方以種類、品質、數量相同之物返還之契約。當事人之一方對他方負金錢或其他代替物之給付義務而約定以之作為消費借貸之標的者，亦成立消費借貸。",
        "summary": "金錢借款通常屬於消費借貸，重點是有交付金錢並約定返還同種類、品質、數量之物。",
    },
    "civil_478": {
        "law_name": "民法",
        "article": "第478條",
        "title": "借用人返還義務",
        "url": "https://mojlaw.moj.gov.tw/LawContentExtent.aspx?LSID=FL001351&LawNo=478&media=print",
        "fallback_text": "借用人應於約定期限內，返還與借用物種類、品質、數量相同之物，未定返還期限者，借用人得隨時返還，貸與人亦得定一個月以上之相當期限，催告返還。",
        "summary": "有約定還款期限時，借用人應於期限內返還；未約定期限時，貸與人可催告返還。",
    },
    "criminal_339": {
        "law_name": "中華民國刑法",
        "article": "第339條",
        "title": "詐欺",
        "url": "https://mojlaw.moj.gov.tw/LawContentExtent.aspx?LSID=FL001424&LawNo=339&media=print",
        "fallback_text": "意圖為自己或第三人不法之所有，以詐術使人將本人或第三人之物交付者，處五年以下有期徒刑、拘役或科或併科五十萬元以下罰金。以前項方法得財產上不法之利益或使第三人得之者，亦同。前二項之未遂犯罰之。",
        "summary": "詐欺通常需要借款當下有詐術、使人陷於錯誤並交付財物；單純借錢後不還通常不足以直接判定為詐欺。",
    },
}

LEGAL_QUESTION_KEYWORDS = [
    "觸犯",
    "哪一條",
    "哪條",
    "犯法",
    "犯罪",
    "告什麼",
    "告哪",
    "法律責任",
    "刑法",
    "民法",
    "詐欺",
]

FRAUD_FACT_KEYWORDS = [
    "一開始就騙",
    "假理由",
    "假資料",
    "假身分",
    "根本沒有還",
    "沒有還款意思",
    "騙我借",
    "詐騙",
    "詐欺",
    "騙錢",
]


def read_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = etree.fromstring(xml)
    return "".join(root.xpath("//w:t/text()", namespaces=NS))


def call_ollama(prompt: str, model: str = MODEL_NAME) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.8,
        },
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "無法連線到 Ollama。請先執行 `ollama serve`，並確認已下載模型："
            "`ollama pull qwen2.5:1.5b`。"
        ) from exc
    return str(data.get("response", "")).strip()


def fetch_url_text(url: str, timeout: int = HTTP_TIMEOUT_SECONDS) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 legal-notice-generator/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="ignore")


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", raw_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>|</div>|</li>|</tr>|</h\d>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def extract_article_text(page_text: str, article: str) -> str:
    article_no = re.search(r"\d+(?:[-之]\d+)?", article)
    if article_no:
        article_pattern = rf"第\s*{re.escape(article_no.group(0))}\s*條"
    else:
        article_pattern = re.escape(article)
    match = re.search(rf"{article_pattern}\s*(.+?)(?:\n\s*第\s*\d+[-之]?\d*\s*條|\n\s*資料來源|\n\s*憲法法庭|\Z)", page_text, flags=re.S)
    if not match:
        return ""
    article_text = re.sub(r"\s+", " ", match.group(1)).strip()
    return article_text[:500]


def clean_body_text(text: str) -> str:
    text = re.sub(r"^```(?:json|text)?", "", text.strip(), flags=re.I)
    text = re.sub(r"```$", "", text.strip())
    text = text.replace("\r", "").replace("\n", "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^存證信函正文[:：]?", "", text)
    text = re.sub(r"^正文[:：]?", "", text)
    if "敬啟者" in text:
        text = text[text.rfind("敬啟者") :]
    for stop in ("郵票", "郵資", "本存證信函共", "經郵局", "備註"):
        if stop in text:
            text = text[: text.find(stop)]
    return text.strip()


def build_ai_prompt(sample_text: str, data: dict[str, str]) -> str:
    _ = sample_text
    fields = json.dumps(data, ensure_ascii=False, indent=2)
    return f"""
你是台灣法律文件助理。系統已讀取一份已填好的存證信函樣本，但樣本只可用來理解正式語氣與格式，不可複製樣本中的任何事實。

要求：
1. 只輸出正文，不要標題、不要 JSON、不要解釋。
2. 文字必須可直接填入中華郵政存證信函格線。
3. 長度必須在 {BODY_CAPACITY} 個中文字元以內，含標點。
4. 使用正式、清楚、可執行的語氣。
5. 不要捏造使用者未提供的事實。
6. 正文只能使用「使用者資料」中的事件、金額、日期、對象、期限與後續措施。
7. 如果使用者沒有提到車、借車、車輛、返還車輛、侵占或民法侵權，不得出現這些詞。

使用者資料：
{fields}
""".strip()


def fallback_body(data: dict[str, str]) -> str:
    event_date = data.get("event_date", "")
    summary = normalize_claim_text(data.get("case_summary", ""))
    request = normalize_claim_text(data.get("request", ""))
    deadline = data.get("deadline", "")
    legal_basis = data.get("legal_basis", "")
    consequence = normalize_consequence(data.get("consequence", ""))

    if summary and not summary.endswith(("。", "，", "；")):
        summary += "。"
    request_sentence = f"請台端於{deadline}{request}"
    if request_sentence and not request_sentence.endswith(("。", "，", "；")):
        request_sentence += "。"
    if legal_basis:
        consequence_sentence = f"若屆期仍未處理，本人將依{legal_basis}{consequence}，請勿自誤。"
    else:
        consequence_sentence = f"若屆期仍未處理，本人將{consequence}，請勿自誤。"
    return (
        f"敬啟者：台端於{event_date}{summary}"
        f"{request_sentence}"
        f"{consequence_sentence}"
    )


def normalize_claim_text(text: str) -> str:
    replacements = {
        "欠我": "積欠本人",
        "還我": "返還本人",
        "我": "本人",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"10萬(?!元)", "10萬元", text)
    return text.strip()


def normalize_consequence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^本人將", "", text)
    text = re.sub(r"^將", "", text)
    return text


def validate_body_against_user_data(body: str, data: dict[str, str]) -> list[str]:
    facts = "".join(str(value) for value in data.values())
    errors: list[str] = []
    forbidden_if_absent = [
        "借車",
        "車輛",
        "車子",
        "歸還車",
        "返還車",
        "侵占",
        "侵權",
        "參萬元",
        "三萬元",
        "三萬",
    ]
    for term in forbidden_if_absent:
        if term in body and term not in facts:
            errors.append(f"AI 正文出現使用者未提供的詞：{term}")

    summary = data.get("case_summary", "")
    request = data.get("request", "")
    if "10萬" in summary + request and ("10萬" not in body and "10萬元" not in body and "十萬" not in body):
        errors.append("AI 正文未保留使用者輸入的 10 萬元金額")
    if not body.startswith("敬啟者："):
        errors.append("正文未使用存證信函常用開頭「敬啟者：」")
    if len(body) >= BODY_CAPACITY and not body.endswith(("。", "！", "？")):
        errors.append("正文疑似超過格線容量後被截斷")
    if "將將" in body:
        errors.append("正文出現重複字：將將")
    informal_phrases = ["您好", "感謝您", "請隨時聯繫", "重要的財產欠款問題", "正式的法律程序"]
    for phrase in informal_phrases:
        if phrase in body:
            errors.append(f"正文出現不適合存證信函格線格式的語句：{phrase}")
    return errors


def ensure_payload_body(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.setdefault("data", {})
    body = clean_body_text(str(data.get("body", "")))
    data["body"] = body
    validation_errors = validate_body_against_user_data(body, data)
    if validation_errors:
        print("\n提醒：既有 JSON 正文不符合存證信函要求，已重新建立正文。")
        for error in validation_errors:
            print(f"- {error}")
        data["body"] = clean_body_text(fallback_body(data))
    return payload


def ask_fields() -> dict[str, str]:
    print("請依序輸入存證信函資料。可先簡短輸入，正文會交由 Qwen 整理。")
    data: dict[str, str] = {}
    for key, label in FIELD_PROMPTS:
        required = key not in {"legal_basis"}
        while True:
            value = input(f"{label}：").strip()
            if value or not required:
                data[key] = value
                break
            print("此欄位必填，請再輸入一次。")
    return data


def collect_data(sample: Path, model: str, no_ai: bool = False) -> dict[str, Any]:
    sample_text = read_docx_text(sample)
    data = ask_fields()
    if no_ai:
        body = fallback_body(data)
    else:
        try:
            body = call_ollama(build_ai_prompt(sample_text, data), model=model)
        except RuntimeError as exc:
            print(f"\n提醒：{exc}")
            print("目前改用固定格式產生正文，之後可重新啟用 Ollama 再跑一次。\n")
            body = fallback_body(data)

    body = clean_body_text(body)
    validation_errors = validate_body_against_user_data(body, data)
    if validation_errors:
        print("\n提醒：AI 正文與使用者資料不一致，已改用規則式正文。")
        for error in validation_errors:
            print(f"- {error}")
        print()
        body = clean_body_text(fallback_body(data))
    if len(body) > BODY_CAPACITY:
        print(f"正文目前 {len(body)} 字，超過 {BODY_CAPACITY} 字上限。")
        print("請重新輸入一版較短正文，或直接按 Enter 讓程式自動截短。")
        manual = input("較短正文：").strip()
        body = clean_body_text(manual) if manual else body[:BODY_CAPACITY]

    data["body"] = body
    return ensure_payload_body({
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "source_sample": str(sample),
        "body_capacity": BODY_CAPACITY,
        "data": data,
    })


def iter_text_nodes(root: etree._Element) -> list[etree._Element]:
    return list(root.xpath("//w:t", namespaces=NS))


def replace_split_placeholders(root: etree._Element, values: dict[str, str]) -> None:
    nodes = iter_text_nodes(root)
    i = 0
    while i <= len(nodes) - 3:
        if nodes[i].text == "{{" and nodes[i + 2].text == "}}":
            key = nodes[i + 1].text or ""
            if key in values:
                nodes[i].text = values[key]
                nodes[i].set(f"{{http://www.w3.org/XML/1998/namespace}}space", "preserve")
                nodes[i + 1].text = ""
                nodes[i + 2].text = ""
                i += 3
                continue
        i += 1


def cell_text(cell: etree._Element) -> str:
    return "".join(cell.xpath(".//w:t/text()", namespaces=NS))


def find_body_tables(root: etree._Element) -> list[etree._Element]:
    tables: list[etree._Element] = []
    for table in root.xpath("//w:tbl", namespaces=NS):
        rows = table.xpath("./w:tr", namespaces=NS)
        if len(rows) != ROWS_PER_COPY + 1:
            continue
        row_cells = [row.xpath("./w:tc", namespaces=NS) for row in rows]
        if any(len(cells) != CHARS_PER_ROW + 1 for cells in row_cells):
            continue
        header = [cell_text(cell) for cell in row_cells[0]]
        markers = [cell_text(row_cells[i][0])[:1] for i in range(1, ROWS_PER_COPY + 1)]
        if header[:3] == ["格行", "1", "2"] and markers == ROW_MARKERS:
            tables.append(table)
    return tables


def set_cell_text(cell: etree._Element, value: str) -> None:
    text_nodes = cell.xpath(".//w:t", namespaces=NS)
    if text_nodes:
        text_nodes[0].text = value
        text_nodes[0].set(f"{{http://www.w3.org/XML/1998/namespace}}space", "preserve")
        for extra in text_nodes[1:]:
            extra.text = ""
        return

    paragraphs = cell.xpath("./w:p", namespaces=NS)
    if not paragraphs:
        paragraphs = [etree.SubElement(cell, f"{{{NS['w']}}}p")]
    paragraph = paragraphs[0]

    # Reuse the nearest run style in the row so the inserted character inherits
    # the original form typography instead of creating a visually different run.
    row = cell.getparent()
    prototype = None
    if row is not None:
        prototype = row.xpath(".//w:r[.//w:t]", namespaces=NS)
    if prototype:
        run = deepcopy(prototype[0])
        for t in run.xpath(".//w:t", namespaces=NS):
            t.text = ""
        text = run.xpath(".//w:t", namespaces=NS)[0]
    else:
        run = etree.Element(f"{{{NS['w']}}}r")
        text = etree.SubElement(run, f"{{{NS['w']}}}t")
    text.text = value
    text.set(f"{{http://www.w3.org/XML/1998/namespace}}space", "preserve")
    paragraph.append(run)


def insert_body(root: etree._Element, body: str) -> None:
    tables = find_body_tables(root)
    if not tables:
        raise ValueError("找不到正文格線表格，無法安全填寫正文。")

    padded = body[:BODY_CAPACITY].ljust(BODY_CAPACITY)
    rows = textwrap.wrap(padded, CHARS_PER_ROW, drop_whitespace=False)

    for table in tables:
        table_rows = table.xpath("./w:tr", namespaces=NS)[1:]
        for row, row_text in zip(table_rows, rows):
            cells = row.xpath("./w:tc", namespaces=NS)
            marker = cell_text(cells[0])[:1]
            set_cell_text(cells[0], marker)
            for cell, ch in zip(cells[1:], row_text):
                set_cell_text(cell, "" if ch == " " else ch)


def fill_docx(template: Path, output: Path, data: dict[str, str]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp.docx")
    shutil.copyfile(template, tmp)

    with zipfile.ZipFile(template, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            content = zin.read(item.filename)
            if item.filename == "word/document.xml":
                root = etree.fromstring(content)
                replace_split_placeholders(root, data)
                insert_body(root, data["body"])
                content = etree.tostring(
                    root,
                    xml_declaration=True,
                    encoding="UTF-8",
                    standalone=True,
                )
            zout.writestr(item, content)

    tmp.replace(output)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def case_documents(payload: dict[str, Any]) -> list[dict[str, str]]:
    data = payload.get("data", {})
    labels = {
        "sender_name": "寄件人姓名",
        "sender_address": "寄件人地址",
        "recipient_name": "收件人姓名",
        "recipient_address": "收件人地址",
        "event_date": "事件日期",
        "case_summary": "事件簡述",
        "request": "請求事項",
        "deadline": "履行期限",
        "legal_basis": "法律依據",
        "consequence": "後續措施",
        "body": "存證信函正文",
    }
    docs: list[dict[str, str]] = []
    for key, label in labels.items():
        value = str(data.get(key, "")).strip()
        if value:
            docs.append({"id": key, "title": label, "text": value})
    docs.append(
        {
            "id": "scope",
            "title": "回答範圍",
            "text": "本助理只回答此存證信函案件、文件格式、內容修改、寄送與相關法律文件問題。",
        }
    )
    return docs


def init_memory(payload: dict[str, Any], memory_path: Path) -> dict[str, Any]:
    memory = {
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "case_json": payload,
        "documents": case_documents(payload),
        "messages": [],
    }
    save_memory(memory_path, memory)
    return memory


def load_or_init_memory(memory_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if memory_path.exists():
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
        memory["case_json"] = payload
        memory["documents"] = case_documents(payload)
        memory["updated_at"] = now_iso()
        save_memory(memory_path, memory)
        return memory
    return init_memory(payload, memory_path)


def save_memory(memory_path: Path, memory: dict[str, Any]) -> None:
    memory["updated_at"] = now_iso()
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")


def append_message(memory: dict[str, Any], role: str, content: str) -> None:
    memory.setdefault("messages", []).append(
        {
            "role": role,
            "content": content,
            "time": now_iso(),
        }
    )
    memory["messages"] = memory["messages"][-40:]


def is_legal_question(query: str) -> bool:
    return any(keyword in query for keyword in LEGAL_QUESTION_KEYWORDS) or any(
        keyword in query for keyword in FRAUD_FACT_KEYWORDS
    )


def has_debt_fact(memory: dict[str, Any]) -> bool:
    data_text = json.dumps(memory.get("case_json", {}).get("data", {}), ensure_ascii=False)
    return any(term in data_text for term in ["欠", "借款", "欠款", "還款", "返還", "萬", "元", "新台幣", "臺幣"])


def has_fraud_fact(query: str, memory: dict[str, Any]) -> bool:
    data_text = json.dumps(memory.get("case_json", {}).get("data", {}), ensure_ascii=False)
    combined = f"{query}\n{data_text}"
    return any(keyword in combined for keyword in FRAUD_FACT_KEYWORDS)


def legal_article_keys_for_case(query: str, memory: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    if has_debt_fact(memory):
        keys.extend(["civil_474", "civil_478"])
    if has_fraud_fact(query, memory) or "詐欺" in query or "刑法" in query:
        keys.append("criminal_339")
    if not keys:
        keys.extend(["civil_474", "civil_478"])
    return list(dict.fromkeys(keys))


def fetch_legal_article(article_key: str, use_web: bool) -> dict[str, str]:
    spec = LAW_ARTICLES[article_key]
    source = {
        "key": article_key,
        "law_name": spec["law_name"],
        "article": spec["article"],
        "title": spec["title"],
        "url": spec["url"],
        "summary": spec["summary"],
        "queried_at": now_iso(),
        "source": "fallback",
        "source_note": "未使用官方即時查詢，採用本機備援條文。",
        "text": spec["fallback_text"],
    }
    if not use_web:
        return source

    try:
        page = fetch_url_text(spec["url"])
        page_text = html_to_text(page)
        article_text = extract_article_text(page_text, spec["article"])
        if article_text:
            source["text"] = article_text
            source["source"] = "official"
            source["source_note"] = "來源為法務部主管法規查詢系統；該網站標示不提供法律諮詢，且法規資料仍以機關公布書面資料為準。"
        else:
            source["source_note"] = "官方頁面可連線，但未能解析條文內容，採用本機備援條文。"
    except Exception as exc:
        source["source_note"] = f"未能即時查詢官方來源，採用本機備援條文。原因：{exc}"
    return source


def resolve_legal_sources(
    memory: dict[str, Any],
    query: str,
    use_web: bool,
    refresh: bool,
) -> list[dict[str, str]]:
    memory.setdefault("legal_sources", {})
    keys = legal_article_keys_for_case(query, memory)
    sources: list[dict[str, str]] = []
    for key in keys:
        existing = memory["legal_sources"].get(key)
        if existing and not refresh:
            sources.append(existing)
            continue
        source = fetch_legal_article(key, use_web=use_web)
        memory["legal_sources"][key] = source
        sources.append(source)
    return sources


def format_legal_answer(query: str, memory: dict[str, Any], sources: list[dict[str, str]]) -> str:
    data = memory.get("case_json", {}).get("data", {})
    has_fraud = has_fraud_fact(query, memory)
    amount = data.get("request") or data.get("case_summary") or "本案款項"
    deadline = data.get("deadline", "存證信函所定期限")

    lines: list[str] = []
    lines.append("依目前資料，這比較像民事上的借款返還或消費借貸問題，不一定構成刑事犯罪。")
    if has_fraud:
        lines.append("但你提到的內容可能涉及借款當下是否有詐術，這時才需要進一步評估刑法詐欺。")
    else:
        lines.append("單純欠款或借錢後未還，通常不能只因未還款就直接判定為詐欺。")

    lines.append("\n可能相關條文：")
    for index, source in enumerate(sources, start=1):
        lines.append(
            f"{index}. {source['law_name']}{source['article']}（{source['title']}）：{source['summary']}"
        )
        lines.append(f"   條文摘要：{source['text']}")
        lines.append(f"   來源：{source['url']}（{source['source_note']}）")

    lines.append("\n套用到目前案件：")
    lines.append(f"- 你目前主張的是「{amount}」，履行期限是「{deadline}」。")
    lines.append("- 若有借據、轉帳紀錄、對話紀錄或對方承認欠款的訊息，通常會比較有利於民事請求。")
    if has_fraud:
        lines.append("- 若要往刑法詐欺方向主張，需要補強對方在借款當下使用詐術、讓你陷於錯誤、因此交付款項的證據。")
    else:
        lines.append("- 若沒有對方借款當下施用詐術的證據，建議先以民事返還借款方向處理。")

    lines.append("\n還需要補充的事實：借款證據、轉帳或交付金錢紀錄、約定還款日、催告紀錄、對方是否一開始就用假理由或假資料借錢。")
    lines.append("以上是法律資料整理，不是律師法律意見；正式提告或起訴前建議讓律師或法律扶助單位確認。")
    return "\n".join(lines)


def important_terms(text: str) -> set[str]:
    terms = set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text))
    for keyword in DOMAIN_KEYWORDS:
        if keyword in text:
            terms.add(keyword)
    amounts = re.findall(r"\d+\s*萬(?:元)?|\d+\s*元", text)
    terms.update(amounts)
    return {term.strip().lower() for term in terms if term.strip()}


def retrieve_context(memory: dict[str, Any], query: str, limit: int = 8) -> list[str]:
    query_terms = important_terms(query)
    candidates: list[tuple[int, str]] = []

    for doc in memory.get("documents", []):
        text = f"{doc.get('title', '')}：{doc.get('text', '')}"
        score = sum(3 for term in query_terms if term and term in text.lower())
        score += sum(1 for keyword in DOMAIN_KEYWORDS if keyword in query and keyword in text)
        if score > 0 or doc.get("id") in {"body", "case_summary", "request", "deadline"}:
            candidates.append((score, text))

    for msg in memory.get("messages", [])[-12:]:
        text = f"{msg.get('role')}：{msg.get('content', '')}"
        score = sum(2 for term in query_terms if term and term in text.lower())
        if score > 0:
            candidates.append((score, text))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in candidates[:limit]]


def is_related_question(query: str, memory: dict[str, Any]) -> bool:
    compact = query.strip()
    if not compact:
        return False
    if any(hint in compact for hint in UNRELATED_HINTS) and not any(
        keyword in compact for keyword in DOMAIN_KEYWORDS
    ):
        return False
    if any(keyword in compact for keyword in DOMAIN_KEYWORDS):
        return True

    data = memory.get("case_json", {}).get("data", {})
    fact_values = [str(value) for value in data.values() if str(value).strip()]
    if any(value and value in compact for value in fact_values):
        return True
    if any(term in compact for term in ["他", "對方", "我", "這個", "這樣", "可以嗎", "怎麼辦"]):
        return bool(memory.get("messages") or data)
    return False


def build_chat_prompt(
    query: str,
    memory: dict[str, Any],
    context: list[str],
    legal_sources: list[dict[str, str]] | None = None,
) -> str:
    data = memory.get("case_json", {}).get("data", {})
    case_summary = json.dumps(data, ensure_ascii=False, indent=2)
    retrieved = "\n".join(f"- {item}" for item in context)
    legal_context = "\n".join(
        f"- {source['law_name']}{source['article']}：{source['summary']}；{source['text']}；來源：{source['url']}"
        for source in (legal_sources or [])
    )
    return f"""
你是專門協助台灣存證信函的 AI 助理。你可以持續對話，但只能回答目前案件、存證信函內容、文件格式、寄送流程、證據整理與相關法律文件問題。

限制：
1. 不回答與本案件或存證信函無關的問題。
2. 不捏造資料；如果資料不足，請明確說需要補充哪些資訊。
3. 不要自稱律師，不要保證法律結果。
4. 回答要簡潔、具體、可執行。
5. 優先根據「案件資料」與「RAG 取回內容」回答。

案件資料：
{case_summary}

RAG 取回內容：
{retrieved}

法律條文來源：
{legal_context}

使用者問題：
{query}
""".strip()


def chat_loop(
    memory: dict[str, Any],
    memory_path: Path,
    model: str,
    use_web_legal_search: bool = True,
    refresh_legal_sources: bool = False,
) -> None:
    print("\n已進入存證信函專題對話模式。輸入 `結束`、`exit` 或 `quit` 可離開。")
    while True:
        query = input("\n你可以繼續詢問本案相關問題：").strip()
        if query.lower() in {"exit", "quit"} or query in {"結束", "離開", "退出"}:
            print("已結束對話。")
            break

        append_message(memory, "user", query)
        if not is_related_question(query, memory):
            answer = "這個問題與目前存證信函案件無關，我只能協助本案、存證信函內容、格式、寄送與相關法律文件問題。"
            print(answer)
            append_message(memory, "assistant", answer)
            save_memory(memory_path, memory)
            continue

        context = retrieve_context(memory, query)
        if is_legal_question(query):
            legal_sources = resolve_legal_sources(
                memory,
                query,
                use_web=use_web_legal_search,
                refresh=refresh_legal_sources,
            )
            answer = format_legal_answer(query, memory, legal_sources)
            print(answer)
            append_message(memory, "assistant", answer)
            save_memory(memory_path, memory)
            continue

        try:
            answer = call_ollama(build_chat_prompt(query, memory, context), model=model)
        except RuntimeError as exc:
            answer = f"目前無法連線到 Ollama，因此不能進行 AI 對話。{exc}"
        answer = answer.strip()
        print(answer)
        append_message(memory, "assistant", answer)
        save_memory(memory_path, memory)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 存證信函 Word 產生器")
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE, help="已填好的存證信函樣本")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="空白存證信函範本")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="輸出的/讀取的 JSON 檔")
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY, help="RAG 記憶檔")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="輸出的 Word 檔")
    parser.add_argument("--model", default=MODEL_NAME, help="Ollama 模型名稱")
    parser.add_argument("--from-json", action="store_true", help="不詢問，直接用既有 JSON 產生 Word")
    parser.add_argument("--no-ai", action="store_true", help="不呼叫 Ollama，使用固定格式產生正文")
    parser.add_argument("--chat", action="store_true", help="不重新產生 Word，直接載入 JSON/RAG 記憶進入對話")
    parser.add_argument("--no-chat", action="store_true", help="產生 Word 後不要進入對話模式")
    parser.add_argument("--no-web-legal-search", action="store_true", help="停用官方法規即時查詢，只使用本機備援條文")
    parser.add_argument("--refresh-legal-sources", action="store_true", help="強制重新查詢官方法規並更新 RAG 記憶")
    args = parser.parse_args()

    if args.chat:
        payload = ensure_payload_body(load_json(args.json))
        save_json(args.json, payload)
        memory = load_or_init_memory(args.memory, payload)
        chat_loop(
            memory,
            args.memory,
            model=args.model,
            use_web_legal_search=not args.no_web_legal_search,
            refresh_legal_sources=args.refresh_legal_sources,
        )
        return 0

    if args.from_json:
        payload = ensure_payload_body(load_json(args.json))
        save_json(args.json, payload)
    else:
        payload = collect_data(args.sample, model=args.model, no_ai=args.no_ai)
        save_json(args.json, payload)
        print(f"\n已輸出 JSON：{args.json}")

    data = payload["data"]
    fill_docx(args.template, args.output, data)
    print(f"已產生 Word：{args.output}")
    print(f"正文共 {len(data['body'])} 字，格線容量 {BODY_CAPACITY} 字。")
    memory = load_or_init_memory(args.memory, payload)
    print(f"已更新 RAG 記憶：{args.memory}")
    if not args.no_chat:
        chat_loop(
            memory,
            args.memory,
            model=args.model,
            use_web_legal_search=not args.no_web_legal_search,
            refresh_legal_sources=args.refresh_legal_sources,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())