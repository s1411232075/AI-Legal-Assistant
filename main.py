import ollama
import json
import re
from docx import Document

# --- 1. 系統設定區 ---
CONFIG = {
    "存證信函": {
        "template": "legal_notice.docx",
        "fields": ["sender_name", "sender_addr", "receiver_name", "receiver_addr", "fact_date", "amount", "deadline"]
    },
    "房屋租賃": {
        "template": "rent_template.docx",
        "fields": ["landlord", "tenant", "address", "start_date", "end_date", "rent", "pay_day", "special_terms"]
    }
}

FIELD_MAP = {
    "sender_name": "您的姓名", "sender_addr": "您的地址",
    "receiver_name": "債務人姓名", "receiver_addr": "債務人地址",
    "amount": "欠款金額", "fact_date": "借款日期", "deadline": "還款期限(天)",
    "landlord": "房東姓名", "tenant": "房客姓名", "address": "房屋地址",
    "start_date": "起租日期", "end_date": "退租日期", "rent": "每月租金",
    "pay_day": "每月交租日", "special_terms": "特別約定事項"
}

# --- 2. 邏輯處理區：Word 生成 ---

def generate_docx(template_name, data, output_name):
    try:
        doc = Document(template_name)
        # 遍歷所有段落與表格
        all_paragraphs = list(doc.paragraphs)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    all_paragraphs.extend(cell.paragraphs)
        
        # 進行標籤替換
        for para in all_paragraphs:
            for key, value in data.items():
                tag = f"{{{{{key}}}}}"
                if tag in para.text:
                    para.text = para.text.replace(tag, str(value))
        
        doc.save(output_name)
        print(f"\n✅ 文件已成功生成：{output_name}")
    except Exception as e:
        print(f"❌ 生成失敗: {e}")

# --- 3. Agent 對話核心 ---

def start_legal_agent():
    user_context = {}
    current_mode = None 
    
    print("⚖️ AI 法律助理已上線。您可以直接描述您的問題（例如：有人欠我錢）。")
    
    while True:
        # A. 找出下一個缺失的欄位
        next_field = None
        if current_mode:
            required = CONFIG[current_mode]['fields']
            missing = [f for f in required if f not in user_context or not str(user_context[f]).strip()]
            if missing:
                next_field = missing[0]

        # B. 取得使用者輸入
        user_input = input("\n👤 您：").strip()
        if not user_input: continue

        # C. 初始模式偵測 (關鍵字判定)
        if not current_mode:
            if any(k in user_input for k in ["欠", "錢", "還", "借"]):
                current_mode = "存證信函"
            elif any(k in user_input for k in ["租", "房", "合約"]):
                current_mode = "房屋租賃"

        # D. 建立具備「上下文感知」的 Prompt
        # 告訴 AI 我們現在在問什麼，它才不會把答案漏掉
        field_instruction = f"目前正在詢問：{FIELD_MAP.get(next_field, '初步描述')}"
        
        prompt = f"""
        你是一個法律數據提取器。
        【目前模式】：{current_mode if current_mode else "未知"}
        【當前任務】：從使用者輸入中提取「{field_instruction}」。
        
        ### 規則 ###
        1. 僅回傳 JSON 格式。
        2. 如果使用者提供的是對「{field_instruction}」的回答，請將其放入對應欄位。
        3. 不要編造任何資訊，沒提到就填 null。

        ### 輸出格式 ###
        {{
            "mode": "{current_mode if current_mode else "null"}",
            "extracted_data": {{
                "{next_field if next_field else 'info'}": "提取到的內容"
            }}
        }}

        使用者輸入："{user_input}"
        """
        
        try:
            # 呼叫 Ollama (請確保背景有開 ollama)
            response = ollama.chat(model='mistral', messages=[{'role': 'user', 'content': prompt}])
            raw_content = response['message']['content']
            
            # 使用 Regex 提取 JSON
            match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            if match:
                res = json.loads(match.group())
                
                # 更新模式
                if not current_mode and res.get('mode') and res['mode'] != "null":
                    current_mode = res['mode']
                
                # 更新數據 (排除無效值)
                new_data = res.get('extracted_data', {})
                for k, v in new_data.items():
                    if v and str(v).strip() not in ["null", "None", "未提供", "未知", ""]:
                        user_context[k] = v
            
            # E. 流程狀態檢查與下一步引導
            if not current_mode:
                print("🤖 AI：我不確定您要處理哪種法律文件。請問是「存證信函」還是「房屋租賃」？")
                continue

            # 再次檢查缺失
            required = CONFIG[current_mode]['fields']
            missing = [f for f in required if f not in user_context]
            
            if not missing:
                print(f"🤖 AI：資料已齊全！正在為您產出「{current_mode}」...")
                generate_docx(CONFIG[current_mode]['template'], user_context, f"Final_{current_mode}.docx")
                break
            else:
                next_f = missing[0]
                # 使用 FIELD_MAP 轉換成白話中文問使用者
                print(f"🤖 AI：已記錄。那請問「{FIELD_MAP.get(next_f, next_f)}」是？")
                
        except Exception as e:
            print(f"⚠️ 處理中發生錯誤，請再試一次...")

if __name__ == "__main__":
    start_legal_agent()
