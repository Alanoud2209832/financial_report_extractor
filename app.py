# -*- coding: utf-8 -*-
# app.py
# ... (باقي الكود)
import streamlit as st
import pandas as pd
import json
import io
import base64
import os
from google import genai
from google.genai.errors import APIError
from db import save_to_db,fetch_all_reports

# ===============================
# 1. إعدادات API
# ... (باقي الكود)
# ===============================
# 1. إعدادات API
# ===============================
# يفضل تحميل هذا من ملف .env في بيئة الإنتاج
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCrzPwjjz7SLMxduGZ9xbO3tqteLDL-wdU") 
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    " أنت نظام استخلاص بيانات آلي (OCR/NLP)التعديل هنا: التركيز على الاستخلاص الحرفي والنسخ الدقيق للبيانات، خاصة في الحقول النصية الطويلة."
    "أنت نظام استخلاص بيانات آلي (OCR/NLP). مهمتك هي قراءة النص والصورة المستخرجة من الوثيقة المالية "
    "وتحويل البيانات إلى كائن JSON وفقاً للمخطط المحدد بدقة. يجب عليك **نسخ** جميع القيم المستخلصة "
    "تماماً كما تظهر في المستند الأصلي، دون تلخيص أو إعادة صياغة، خاصةً في حقل 'سبب الاشتباه'. "
    "قم بتصحيح أي انعكاس أو تشويش في النص العربي قبل الاستخلاص. استخدم القيمة 'غير متوفر' للحقول غير الموجودة."
)

# تم تعديل الاسم الطويل ليصبح "إجمالي إيداع الدراسة" ليتوافق مع قاعدة البيانات
REPORT_FIELDS_ARABIC = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة"
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

        with st.spinner(f"⏳ جاري الاستخلاص من '{file_name}'..."):
            response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)

        extracted_data = json.loads(response.text)
        extracted_data['اسم الملف'] = file_name
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success(f"✅ تم الاستخلاص من '{file_name}' بنجاح!")
        return extracted_data

    except Exception as e:
        st.error(f"❌ خطأ أثناء الاستخلاص: {e}")
        return None


# Excel Export: جلب البيانات من قاعدة البيانات
def create_final_report_from_db(records, column_names):
    import xlsxwriter
    if not records: 
        st.warning("لا توجد بيانات في قاعدة البيانات لتصديرها.")
        return None

    # إنشاء DataFrame من البيانات المسترجعة
    df = pd.DataFrame(records, columns=column_names)
    
    # إضافة عمود الترقيم
    df.insert(0, '#', range(1, len(df) + 1))
    
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    sheet_name = 'التقرير المالي من قاعدة البيانات'
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    
    # تنسيق Excel
    workbook, worksheet = writer.book, writer.sheets[sheet_name]
    worksheet.right_to_left()
    col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
    
    for i, col_name in enumerate(df.columns):
        if col_name == 'سبب الاشتباه':
            worksheet.set_column(i, i, 120, col_format)
        else:
            width = 25 if col_name in ["اسم المشتبه به", "رقم صاحب العمل/ السجل التجاري", "اسم الملف", "وقت الاستخلاص"] else 18
            worksheet.set_column(i, i, width, col_format)
            
    writer.close()
    output.seek(0)
    return output.read()


# ===============================
# 3. واجهة المستخدم
# ===============================
def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")

    uploaded_files = st.file_uploader(
        "قم بتحميل الملفات (يمكنك اختيار أكثر من ملف)",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        all_extracted_data = []

        if st.button("بدء الاستخلاص"):
            for uploaded_file in uploaded_files:
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()
                st.info(f"جاري معالجة: **{file_name}**")
                data = extract_financial_data(file_bytes, file_name, file_type)
                if data:
                    all_extracted_data.append(data)

            if all_extracted_data:
                st.subheader("✏️ جميع البيانات المستخلصة (قابلة للتعديل)")

                df = pd.DataFrame(all_extracted_data)

                # إضافة العمودين المضافين في app.py إلى DataFrame المعروض إذا لم يكونا موجودين
                for col in ["اسم الملف", "وقت الاستخلاص"]:
                    if col not in df.columns: df[col] = 'غير متوفر'
                
                # ترتيب الأعمدة للعرض
                display_cols = ["اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
                df = df.reindex(columns=display_cols, fill_value='غير متوفر')

                edited_df = st.data_editor(
                    df,
                    use_container_width=True,
                    num_rows="dynamic"
                )

                st.markdown("---")

                if st.button("✔️ تأكيد وحفظ التعديلات في قاعدة البيانات"):
                    saved_count = 0
                    for _, row in edited_df.iterrows():
                        if save_to_db(dict(row)):
                            saved_count += 1
                    
                    if saved_count > 0:
                         st.success(f"✅ تم حفظ {saved_count} سجل بنجاح في قاعدة البيانات!")
                    else:
                         st.warning("⚠️ لم يتم حفظ أي سجل. تحقق من أخطاء الاتصال أو البيانات.")


    # ----------------------------------------------------
    # قسم التصدير من قاعدة البيانات (يظهر دائماً)
    # ----------------------------------------------------
    st.markdown("---")
    st.subheader("📊 تصدير البيانات النهائية")

    if st.button("⬇️ تحميل تقرير Excel من قاعدة البيانات"):
        report_data = fetch_all_reports()
        
        if report_data and report_data[0]: # التحقق من وجود سجلات
            records, column_names = report_data
            
            with st.spinner("⏳ جاري إنشاء ملف Excel من البيانات المحفوظة..."):
                excel_data_bytes = create_final_report_from_db(records, column_names)
            
            if excel_data_bytes:
                st.download_button(
                    "⬇️ اضغط للتحميل",
                    data=excel_data_bytes,
                    file_name="Final_Database_Report.xlsx",
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            else:
                st.warning("لم يتم إنشاء ملف Excel. قد تكون البيانات المسترجعة فارغة.")
        else:
            st.error("فشل في استرجاع البيانات من قاعدة البيانات أو لا توجد سجلات.")


if __name__ == "__main__":
    main()
