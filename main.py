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

# --- 2. 邏輯處理區 ---

def generate_docx(template_name, data, output_name):
    try:
        doc = Document(template_name)
        for paragraph in doc.paragraphs:
            for key, value in data.items():
                tag = f"{{{{{key}}}}}"
                if tag in paragraph.text:
                    paragraph.text = paragraph.text.replace(tag, str(value))
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        for key, value in data.items():
                            tag = f"{{{{{key}}}}}"
                            if tag in paragraph.text:
                                paragraph.text = paragraph.text.replace(tag, str(value))
                                
        doc.save(output_name)
        print(f"\n 文件已成功生成：{output_name}")
    except Exception as e:
        print(f" 生成失敗: {e}")

# --- 3. Agent 對話核心 ---

def start_legal_agent():
    user_context = {}
    current_mode = None 
    
    print("⚖️ AI 法律助理已上線。您可以直接描述您的問題。")
    
    while True:
        user_input = input("\n👤 您：")
        
        # 找出目前還缺什麼，直接餵給 Prompt 讓 AI 知道它的目標
        missing_list = []
        if current_mode in CONFIG:
            missing_list = [f for f in CONFIG[current_mode]['fields'] if f not in user_context]

        # --- 核心優化：Prompt Engineering ---
        prompt = f"""
        ### 任務 ###
        你是法律數據提取器。從輸入中抓取資訊填入 JSON。
        
        ### 強制範例 (ONE-SHOT) ###
        使用者輸入: "我叫張三住台北，欠款人李四住桃園，欠五萬，去年1月借的，限3天還。"
        輸出: {{
            "mode": "存證信函",
            "new_data": {{
                "sender_name": "張三",
                "sender_addr": "台北",
                "receiver_name": "李四",
                "receiver_addr": "桃園",
                "amount": "五萬",
                "fact_date": "去年1月",
                "deadline": "3"
            }}
        }}
        
        ### 當前目標 ###
        - 待收集欄位: {missing_list}
        - 使用者輸入: "{user_input}"
        
        請嚴格以上述範例格式輸出 JSON，不要解釋。
        """
        
        try:
            response = ollama.chat(model='mistral', messages=[{'role': 'user', 'content': prompt}])
            raw = response['message']['content']
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            
            if match:
                res = json.loads(match.group())
                if res.get('mode') and not current_mode:
                    current_mode = res['mode']
                if res.get('new_data'):
                    # 小細節：在更新前，可以印出來看 AI 抓了什麼
                    # print(f"--- AI 提取到: {res['new_data']} ---")
                    user_context.update(res['new_data'])
            
            # --- 流程控制 ---
            if not current_mode:
                if "錢" in user_input or "欠" in user_input: current_mode = "存證信函"
                elif "租" in user_input: current_mode = "房屋租賃"
                else:
                    print("🤖 AI：了解，請問您是要處理存證信函還是房屋租賃？")
                    continue

            required = CONFIG[current_mode]['fields']
            missing = [f for f in required if f not in user_context]
            
            if not missing:
                print(f"🤖 AI：資料已齊全！正在產出「{current_mode}」...")
                generate_docx(CONFIG[current_mode]['template'], user_context, f"Final_{current_mode}.docx")
                break
            else:
                next_f = missing[0]
                print(f"🤖 AI：已記錄。那請問「{FIELD_MAP.get(next_f, next_f)}」是多少呢？")
                
        except Exception as e:
            print(f"⚠️ 處理中，請繼續提供資訊...")

if __name__ == "__main__":
    start_legal_agent()