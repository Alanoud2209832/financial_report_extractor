# -*- coding: utf-8 -*-
# app.py
import streamlit as st
import pandas as pd
import json
import io
import base64
import os
import re 
import pytz # 💡 تمت الإضافة هنا
from google import genai
from google.genai.errors import APIError
from db import save_to_db, fetch_all_reports

# ===============================
# 1. إعدادات API
# ===============================
# يفضل تحميل هذا من ملف .env في بيئة الإنتاج
# **تنبيه**: يرجى استخدام os.getenv("GEMINI_API_KEY") وتجنب وضع المفتاح مباشرة
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyDDDRIUK4lQisl4Ik0VWZWc7OI3F8VsVhs") 
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (OCR/NLP). مهمتك هي قراءة النص والصورة المستخرجة من الوثيقة المالية "
    "وتحويل البيانات إلى كائن JSON وفقاً للمخطط المحدد بدقة. يجب عليك **نسخ** جميع القيم المستخلصة "
    "تماما كما تظهر في المستند الأصلي، دون تلخيص أو إعادة صياغة، خاصةً في حقل 'سبب الاشتباه'. "
    "قم بتصحيح أي انعكاس أو تشويش في النص العربي قبل الاستخلاص. استخدم القيمة 'غير متوفر' للحقول غير الموجودة."
)

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

# 💡 دالة مساعدة لتحويل الأرقام العربية إلى إنجليزية
def arabic_to_english_numbers(text):
    if not isinstance(text, str):
        return text
    arabic_map = {'٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
                  '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'}
    return text.translate(str.maketrans(arabic_map))

# 💡 دالة التحقق من التشتت (المؤشر)
def check_for_suspicion(data):
    """يضيف علامة 'مؤشر التشتت' (🔴) للبيانات المشكوك فيها."""
    suspicion_indicator = ""
    
    # --- 1. التحقق من التواريخ الهجرية (المثال: 0945/06/20) ---
    date_fields = ["تاريخ الصادر", "تاريخ الوارد"]
    for field in date_fields:
        date_val = data.get(field, "")
        try:
            # تنظيف الأرقام العربية وتحويلها إلى إنجليزية
            date_str_en = arabic_to_english_numbers(str(date_val))
            
            # محاولة استخراج السنة باستخدام فواصل متعددة
            parts = re.split(r'[/\-.]', date_str_en)
            if len(parts) == 3:
                # إزالة أي أحرف غير رقمية من الجزء الأول (السنة)
                year_str = re.sub(r'[^\d]', '', parts[0])
                year = int(year_str) if year_str else 0
                
                # المعيار: إذا كانت السنة الهجرية غير مكتملة أو خارج النطاق 1400-1500
                if year < 1400 or year > 1500:
                    # هذه الحالة ستلتقط 0945/06/20 كـ 945
                    suspicion_indicator += f"🔴 ({field}: سنة غير طبيعية) "
        except Exception:
            # إذا فشل التحويل بالكامل (مثل القيمة النصية)
            if str(date_val).strip() not in ['غير متوفر', '']:
                 suspicion_indicator += f"🔴 ({field}: صيغة غير مفهومة) "
            pass

    # --- 2. التحقق من القيم المالية المستخلصة كصفر ---
    financial_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    for field in financial_fields:
        val = data.get(field, "")
        if str(val).strip() in ['0', '0.00', '٠', '٠,٠٠']:
             suspicion_indicator += f"⚠️ ({field} = 0) "

    return suspicion_indicator.strip() or "✅ سليم"

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
        
        # 💡 التعديل هنا: استخدام المنطقة الزمنية "Asia/Riyadh" (توقيت السعودية)
        riyadh_tz = pytz.timezone('Asia/Riyadh')
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now(tz=riyadh_tz).strftime("%Y-%m-%d %H:%M:%S")

        # إضافة مؤشر التشتت
        extracted_data['مؤشر التشتت'] = check_for_suspicion(extracted_data) 
        
        st.success(f"✅ تم الاستخلاص من '{file_name}' بنجاح!")
        return extracted_data

    except Exception as e:
        st.error(f"❌ خطأ أثناء الاستخلاص: {e}")
        return None

def create_final_report_from_db(records, column_names):
    import xlsxwriter
    if not records: 
        st.warning("لا توجد بيانات في قاعدة البيانات لتصديرها.")
        return None

    df = pd.DataFrame(records, columns=column_names)
    df.insert(0, '#', range(1, len(df) + 1))
    
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    
    # تصحيح الخطأ: استخدام اسم ورقة عمل لا يتجاوز 31 حرفاً
    sheet_name = 'التقرير المالي النهائي' 
    
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

    st.title("استخلاص وتقارير مالية مدعومة بالذكاء الاصطناعي 🤖")
    st.markdown("---")

    uploaded_files = st.file_uploader(
        "قم بتحميل الملفات (يمكنك اختيار أكثر من ملف)",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        all_extracted_data = []

        if 'extracted_data_df' not in st.session_state:
            st.session_state['extracted_data_df'] = pd.DataFrame()

        if st.button("بدء الاستخلاص"):
            for uploaded_file in uploaded_files:
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()
                st.info(f"جاري معالجة: **{file_name}**")
                data = extract_financial_data(file_bytes, file_name, file_type)
                if data:
                    all_extracted_data.append(data)

            if all_extracted_data:
                new_df = pd.DataFrame(all_extracted_data)
                
                display_cols = ["مؤشر التشتت", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
                new_df = new_df.reindex(columns=display_cols, fill_value='غير متوفر')
                
                st.session_state['extracted_data_df'] = pd.concat([st.session_state['extracted_data_df'], new_df], ignore_index=True)


        if not st.session_state['extracted_data_df'].empty:
            st.subheader("✏️ جميع البيانات المستخلصة (قابلة للتعديل)")

            edited_df = st.data_editor(
                st.session_state['extracted_data_df'],
                use_container_width=True,
                num_rows="dynamic"
            )

            st.markdown("---")

            # 💡 منطق الحفظ والتوقف عند أول خطأ
            if st.button("✔️ تأكيد وحفظ التعديلات في قاعدة البيانات"):
                saved_count = 0
                total_rows = len(edited_df)
                status_placeholder = st.empty() 

                for index, row in edited_df.iterrows():
                    # تحويل الصف إلى قاموس
                    row_data = dict(row)
                    
                    # 💡 الخطوة الحاسمة: حذف عمود "مؤشر التشتت" قبل الحفظ
                    if 'مؤشر التشتت' in row_data:
                        del row_data['مؤشر التشتت']
                        
                    if save_to_db(row_data): # تمرير القاموس النظيف
                        saved_count += 1
                    else:
                        status_placeholder.error(f"❌ فشل الحفظ عند السجل رقم {index + 1}. تم إيقاف العملية.")
                        break # توقف عند أول خطأ

                if saved_count == total_rows:
                    status_placeholder.success(f"✅ تم حفظ {saved_count} سجل بنجاح في قاعدة البيانات!")
                    # مسح البيانات من الجلسة بعد الحفظ الناجح
                    st.session_state['extracted_data_df'] = pd.DataFrame()
                    st.rerun() 
                elif saved_count > 0:
                    status_placeholder.warning(f"⚠️ تم حفظ {saved_count} سجل بنجاح. فشل حفظ السجلات المتبقية بسبب الخطأ أعلاه.")
                elif saved_count == 0 and total_rows > 0:
                     status_placeholder.error("❌ فشل حفظ جميع السجلات. يرجى مراجعة رسائل الخطأ الحمراء أعلاه.")


    # ----------------------------------------------------
    # قسم التصدير من قاعدة البيانات
    # ----------------------------------------------------
    st.markdown("---")
    st.subheader("📊 تصدير البيانات النهائية")

    if st.button("⬇️ تحميل تقرير Excel من قاعدة البيانات"):
        report_data = fetch_all_reports()
        
        if report_data and report_data[0] is not None: 
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
