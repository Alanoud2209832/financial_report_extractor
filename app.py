# app.py
import streamlit as st
import pandas as pd
import json
import io
import base64
from google import genai
from google.genai.errors import APIError
from db import save_to_db

# ===============================
# 1. إعدادات API
# ===============================
GEMINI_API_KEY = "AIzaSyA06G-4CqtJtXqJoAdCXMDGtjaoh3DA-qI"
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (OCR/NLP)..."
)

REPORT_FIELDS_ARABIC = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي الإيداع على الحساب اثناء الدراسة"
]

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        field: {"type": "STRING", "description": f"القيمة المستخلصة لـ: {field}"}
        for field in REPORT_FIELDS_ARABIC
    },
    "propertyOrdering": REPORT_FIELDS_ARABIC
}

# ===============================
# 2. وظائف المعالجة
# ===============================
def extract_financial_data(file_bytes, file_name, file_type):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        mime_type = "application/pdf" if file_type=='pdf' else f"image/{'jpeg' if file_type=='jpg' else file_type}"

        content_parts = [
            "قم باستخلاص جميع البيانات...",
            {"inlineData": {"data": base64.b64encode(file_bytes).decode('utf-8'), "mimeType": mime_type}}
        ]

        config = {
            "systemInstruction": SYSTEM_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA
        }

        with st.spinner(f"⏳ جاري استخلاص البيانات من '{file_name}'..."):
            response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)

        extracted_data = json.loads(response.text)
        extracted_data['اسم الملف'] = file_name
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success(f"✅ تم الاستخلاص من '{file_name}' بنجاح!")
        return extracted_data

    except Exception as e:
        st.error(f"❌ خطأ أثناء الاستخلاص: {e}")
        return None

def create_final_report(extracted_data):
    import xlsxwriter
    if not extracted_data: return None

    df = pd.DataFrame([extracted_data])
    df.insert(0, '#', 1)

    column_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
    for col in column_order:
        if col not in df.columns: df[col] = 'غير متوفر'
    df = df[column_order]

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, sheet_name='التقرير المالي', index=False)
    workbook, worksheet = writer.book, writer.sheets['التقرير المالي']
    worksheet.right_to_left()
    col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
    worksheet.set_column('U:U', 120, col_format)
    for i, col_name in enumerate(column_order):
        if col_name != 'سبب الاشتباه': width = 25 if col_name in ["اسم المشتبه به","رقم صاحب العمل/ السجل التجاري"] else 18; worksheet.set_column(i,i,width,col_format)
    writer.close()
    output.seek(0)
    return output.read()

# ===============================
# 3. واجهة المستخدم
# ===============================
def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")
    uploaded_file = st.file_uploader("قم بتحميل ملف التقرير", type=["pdf","png","jpg","jpeg"])
    if uploaded_file:
        file_bytes, file_name = uploaded_file.read(), uploaded_file.name
        file_type = file_name.split('.')[-1].lower()
        st.success(f"تم تحميل ملف: **{file_name}**")

        if st.button("بدء الاستخلاص والتحويل إلى Excel"):
            extracted_data = extract_financial_data(file_bytes, file_name, file_type)
            if extracted_data:
                st.subheader("✅ البيانات المستخلصة")
                df_display = pd.DataFrame([extracted_data])
                st.dataframe(df_display, use_container_width=True, height=200)

                excel_data_bytes = create_final_report(extracted_data)
                if excel_data_bytes:
                    st.download_button(
                        "⬇️ تحميل ملف Excel",
                        data=excel_data_bytes,
                        file_name=f"{file_name.replace(f'.{file_type}','')}_Extracted_Report.xlsx",
                        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    )

                # زر الحفظ في Neon
                if st.button("💾 حفظ البيانات في قاعدة Neon"):
                    success = save_to_db(extracted_data)
                    if success: st.success("✅ تم حفظ البيانات بنجاح في Neon!")

if __name__ == '__main__':
    main()
