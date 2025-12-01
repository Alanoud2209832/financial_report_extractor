import streamlit as st
import pandas as pd
import json
import io
import base64
from google import genai
from google.genai.errors import APIError
from db import save_to_db

# ----------------------------------------------------------------
# 1. إعدادات API والثوابت
# ----------------------------------------------------------------

GEMINI_API_KEY = "AIzaSyA06G-4CqtJtXqJoAdCXMDGtjaoh3DA-qI"  # استبدلي بالمفتاح الصالح
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (OCR/NLP). مهمتك هي قراءة النص والصورة المستخرجة من الوثيقة المالية "
    "وتحويل البيانات إلى كائن JSON وفقاً للمخطط المحدد بدقة. يجب عليك **نسخ** جميع القيم المستخلصة "
    "تماماً كما تظهر في المستند الأصلي، دون تلخيص أو إعادة صياغة، خاصةً في حقل 'سبب الاشتباه'. "
    "قم بتصحيح أي انعكاس أو تشويش في النص العربي قبل الاستخلاص. استخدم القيمة 'غير متوفر' للحقول غير الموجودة."
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
    "properties": {field: {"type": "STRING", "description": f"القيمة المستخلصة لـ: {field}"} for field in REPORT_FIELDS_ARABIC},
    "propertyOrdering": REPORT_FIELDS_ARABIC
}

# ----------------------------------------------------------------
# 2. وظائف المعالجة
# ----------------------------------------------------------------

def extract_financial_data(file_bytes, file_name, file_type):
    """تستخدم Gemini API لاستخلاص البيانات المالية مباشرة من بيانات الملف."""
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        if file_type == 'pdf':
            mime_type = "application/pdf"
            st.warning("⚠️ جاري إرسال ملف PDF مباشرةً. قد يستغرق التحليل وقتاً أو يفشل في ملفات PDF المعقدة.")
        elif file_type in ['png', 'jpg', 'jpeg']:
            mime_type = f"image/{'jpeg' if file_type == 'jpg' else file_type}"
        else:
            st.error(f"نوع الملف غير مدعوم: {file_type}")
            return None

        content_parts = [
            "قم باستخلاص جميع البيانات من هذه الوثيقة المالية "
            "وحوّلها إلى كائن JSON يطابق المخطط المحدد بدقة. "
            "يرجى استخدام الحقول العربية المطلوبة كمفاتيح JSON. "
            "إذا لم تتمكن من العثور على قيمة حقل معين، ضع القيمة: 'غير متوفر'.",
            {"inlineData": {"data": base64.b64encode(file_bytes).decode('utf-8'), "mimeType": mime_type}}
        ]

        config = {
            "systemInstruction": SYSTEM_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        }

        with st.spinner(f"⏳ جاري استخلاص البيانات من '{file_name}'..."):
            response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)

        extracted_data = json.loads(response.text)
        extracted_data['اسم الملف'] = file_name
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success(f"✅ تم استخلاص البيانات من التقرير: '{file_name}' بنجاح!")

        return extracted_data

    except APIError as e:
        st.error(f"🚨 خطأ في الاتصال بـ Gemini API: {e}")
    except json.JSONDecodeError:
        st.error("❌ فشل في تفسير استجابة النموذج كـ JSON.")
    except Exception as e:
        st.error(f"❌ حدث خطأ غير متوقع: {e}")
    return None

def create_final_report(extracted_data):
    """تحويل البيانات المستخلصة إلى ملف Excel (XLSX) بتنسيق RTL."""
    import xlsxwriter
    if not extracted_data:
        return None

    column_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
    df = pd.DataFrame([extracted_data])
    df.insert(0, '#', 1)
    final_cols = [col if col in df.columns else 'غير متوفر' for col in column_order]
    df = df[final_cols]

    output = io.BytesIO()
    try:
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        df.to_excel(writer, sheet_name='التقرير المالي', index=False)
        workbook = writer.book
        worksheet = writer.sheets['التقرير المالي']
        worksheet.right_to_left()
        col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
        worksheet.set_column('U:U', 120, col_format)

        for i, col_name in enumerate(final_cols):
            if col_name != 'سبب الاشتباه':
                width = 25 if col_name in ["اسم المشتبه به", "رقم صاحب العمل/ السجل التجاري"] else 18
                worksheet.set_column(i, i, width, col_format)

        writer.close()
        output.seek(0)
        return output.read()
    except Exception as e:
        st.error(f"🚨 حدث خطأ أثناء إنشاء ملف Excel: {e}")
        return None

# ----------------------------------------------------------------
# 3. واجهة المستخدم
# ----------------------------------------------------------------

def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")

    st.markdown("""
    <style>
        .stApp { background-color: #f0f2f6; }
        .stButton>button {
            background-color: #1a73e8;
            color: white; 
            border-radius: 8px;
            padding: 10px 20px;
            font-size: 16px;
            font-weight: bold;
            transition: background-color 0.3s;
        }
        .stButton>button:hover { background-color: #1558b5; }
    </style>
    """, unsafe_allow_html=True)

    if not GEMINI_API_KEY:
        st.error("❌ يجب إدخال GEMINI_API_KEY داخل الكود.")
        return

    uploaded_file = st.file_uploader("قم بتحميل ملف التقرير", type=["pdf", "png", "jpg", "jpeg"])
    if uploaded_file is None:
        st.info("يرجى تحميل تقرير مالي لبدء التحليل.")
        return

    file_bytes = uploaded_file.read()
    file_name = uploaded_file.name
    file_type = file_name.split('.')[-1].lower()
    st.success(f"تم تحميل ملف: **{file_name}**")

    if st.button("بدء الاستخلاص والتحويل إلى Excel", key="start_extraction"):
        extracted_data = extract_financial_data(file_bytes, file_name, file_type)

        if not extracted_data:
            st.warning("لم يتم استخلاص أي بيانات.")
            return

        st.subheader("✅ البيانات المستخلصة (جاهزة للتنزيل والحفظ)")
        df_display = pd.DataFrame([extracted_data])
        if 'اسم الملف' in df_display.columns: del df_display['اسم الملف']
        if 'وقت الاستخلاص' in df_display.columns: del df_display['وقت الاستخلاص']
        st.dataframe(df_display, use_container_width=True, height=200)

        # حفظ تلقائي في Neon
        if save_to_db(extracted_data):
            st.success("💾 تم حفظ البيانات تلقائيًا في قاعدة Neon!")
        else:
            st.error("❌ فشل في حفظ البيانات في Neon.")

        # إنشاء ملف Excel للتنزيل
        excel_data_bytes = create_final_report(extracted_data)
        if excel_data_bytes:
            st.subheader("ملف Excel جاهز للتحميل")
            st.download_button(
                label="⬇️ تحميل ملف التقرير النهائي (Excel XLSX)",
                data=excel_data_bytes,
                file_name=f"{file_name.replace('.pdf', '').replace(f'.{file_type}', '')}_Extracted_Report.xlsx",
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )

if __name__ == '__main__':
    main()
