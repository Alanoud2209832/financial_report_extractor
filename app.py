import streamlit as st
import pandas as pd
import json
import io
import base64
from google import genai
from google.genai.errors import APIError
# تأكدي من أن ملف db.py موجود وجاهز للعمل
from db import save_to_db 

# ===============================
# 1. إعدادات API والنظام
# ===============================
# تأكد من تعيين المفتاح هنا أو عبر متغيرات البيئة
GEMINI_API_KEY = "AIzaSyA5ChIhrl9Tlob2NXyUwcau5vK75sIj-gI" 
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'

# البرومت المستخدم لتقسيم النص (Segmenting Prompt)
SEGMENTATION_PROMPT = (
    "أنت محلل وثائق آلي. تم تزويدك بالنص الكامل لوثيقة تحتوي على قضايا مالية متعددة. "
    "كل قضية تبدأ بعبارة واضحة مثل 'بسم الله الرحمن الرحيم' أو 'رئاسة أمن الدولة' أو ظهور 'رقم الصادر'. "
    "مهمتك هي تقسيم النص إلى قائمة من القضايا الفردية (segments). "
    "يرجى إعادة النص مقسماً كقائمة JSON، حيث كل عنصر هو النص الكامل للقضية الواحدة. "
    "لا تقم بتلخيص أو تغيير النص، فقط قم بالتقسيم وإرجاع JSON. "
    "ملاحظة: تجاهل أي رؤوس أو تذييلات مكررة بين القضايا."
)

# البرومت المستخدم للاستخلاص (Extraction Prompt)
SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (OCR/NLP)التعديل هنا: التركيز على الاستخلاص الحرفي والنسخ الدقيق للبيانات، خاصة في الحقول النصية الطويلة."
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
    "properties": {
        field: {"type": "STRING", "description": f"القيمة المستخلصة لـ: {field}"}
        for field in REPORT_FIELDS_ARABIC
    },
    "propertyOrdering": REPORT_FIELDS_ARABIC
}

SEGMENTATION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "cases": {
            "type": "ARRAY",
            "description": "قائمة بالنصوص الكاملة لكل قضية منفصلة.",
            "items": {"type": "STRING"}
        }
    }
}

# ===============================
# 2. وظائف المعالجة الجديدة والمحدثة
# ===============================

def segment_document_by_cases(file_bytes, file_name):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        content_parts = [
            SEGMENTATION_PROMPT,
            {"inlineData": {"data": base64.b64encode(file_bytes).decode('utf-8'), "mimeType": "application/pdf"}}
        ]
        
        config = {
            "systemInstruction": SEGMENTATION_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": SEGMENTATION_SCHEMA
        }

        with st.spinner(f"⏳ جاري تحليل وتقسيم القضايا في '{file_name}'..."):
            response = client.models.generate_content(
                model=MODEL_NAME, 
                contents=content_parts, 
                config=config
            )

        segment_data = json.loads(response.text)
        
        if 'cases' in segment_data and isinstance(segment_data['cases'], list):
            st.success(f"✅ تم تقسيم '{file_name}' إلى {len(segment_data['cases'])} قضية بنجاح.")
            return segment_data['cases'] 
        else:
            st.warning(f"⚠️ فشل التقسيم التلقائي. سيتم التعامل مع الملف بالكامل كقضية واحدة.")
            return [file_bytes] 
            
    except APIError as e:
        st.error(f"❌ خطأ في الاتصال بـ Gemini API أثناء التقسيم: {e}")
        return [file_bytes]
    except Exception as e:
        st.error(f"❌ خطأ غير متوقع أثناء تقسيم الوثيقة: {e}")
        return [file_bytes]


def extract_financial_data(case_text_or_bytes, case_name, file_type, is_segment=False):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        if is_segment:
            content_parts = [
                "استخرج البيانات المطلوبة من النص المرفق. النص يمثل قضية واحدة كاملة.",
                {"text": case_text_or_bytes}
            ]
        else:
            mime_type = "application/pdf" if file_type=='pdf' else f"image/{'jpeg' if file_type=='jpg' else file_type}"
            content_parts = [
                "قم باستخلاص جميع البيانات...",
                {"inlineData": {"data": base64.b64encode(case_text_or_bytes).decode('utf-8'), "mimeType": mime_type}}
            ]

        config = {
            "systemInstruction": SYSTEM_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA
        }

        with st.spinner(f"⏳ جاري استخلاص معلومات القضية: '{case_name}'..."):
            response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)

        extracted_data = json.loads(response.text)
        
        extracted_data['اسم الملف'] = case_name
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

        st.success(f"✅ تم استخلاص معلومات '{case_name}' بنجاح!")
        return extracted_data

    except Exception as e:
        st.error(f"❌ خطأ أثناء الاستخلاص من '{case_name}': {e}")
        return {'اسم الملف': case_name, 'وقت الاستخلاص': pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"), 'رقم الصادر': 'خطأ في الاستخلاص'}


def create_final_report_multiple(all_data):
    import xlsxwriter
    if not all_data: return None

    df_list = []
    for i, data in enumerate(all_data, 1):
        data_copy = data.copy()
        data_copy['#'] = i
        df_list.append(data_copy)

    df = pd.DataFrame(df_list)

    column_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
    
    df = df.reindex(columns=column_order, fill_value='غير متوفر')

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, sheet_name='التقرير المالي', index=False)
    workbook, worksheet = writer.book, writer.sheets['التقرير المالي']
    worksheet.right_to_left()
    col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
    worksheet.set_column('U:U', 120, col_format)
    for i, col_name in enumerate(column_order):
        if col_name != 'سبب الاشتباه':
            width = 25 if col_name in ["اسم المشتبه به","رقم صاحب العمل/ السجل التجاري"] else 18
            worksheet.set_column(i,i,width,col_format)
    writer.close()
    output.seek(0)
    return output.read()

# ===============================
# 3. واجهة المستخدم
# ===============================

def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")
    st.title("أداة استخلاص وتقارير القضايا")

    uploaded_files = st.file_uploader(
        "قم بتحميل الملفات (يمكن اختيار ملف واحد يحتوي على عدة قضايا)",
        type=["pdf","png","jpg","jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        all_extracted_data = []

        if st.button("بدء الاستخلاص والتحويل إلى Excel"):
            
            for uploaded_file in uploaded_files:
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()
                st.info(f"جاري معالجة الملف الأساسي: **{file_name}**")

                if file_type == 'pdf' or file_type in ['png', 'jpg', 'jpeg']:
                    
                    case_segments_or_bytes = segment_document_by_cases(file_bytes, file_name)
                    
                    is_segment_mode = all(isinstance(item, str) for item in case_segments_or_bytes)
                    
                    if is_segment_mode and len(case_segments_or_bytes) > 0:
                        st.subheader(f"تم العثور على {len(case_segments_or_bytes)} قضية في الملف.")
                        for i, case_content in enumerate(case_segments_or_bytes):
                            case_name = f"{file_name} (قضية #{i+1})"
                            data = extract_financial_data(case_content, case_name, file_type, is_segment=True)
                            if data:
                                all_extracted_data.append(data)
                                save_to_db(data)
                    else:
                        st.warning(f"تم التعامل مع '{file_name}' كقضية واحدة (أو فشل التقسيم). جاري الاستخلاص...")
                        data = extract_financial_data(file_bytes, file_name, file_type, is_segment=False)
                        if data:
                            all_extracted_data.append(data)
                            save_to_db(data)
                
                else:
                    st.error(f"نوع الملف {file_type} غير مدعوم للمعالجة.")


            if all_extracted_data:
                st.subheader("✅ جميع البيانات المستخلصة")
                df_display = pd.DataFrame(all_extracted_data)
                
                df_display.insert(0, '#', range(1, 1 + len(df_display)))

                cols_to_display = ["#", "اسم الملف", "رقم الصادر", "اسم المشتبه به", "رقم الهوية"]
                
                df_safe_display = df_display.reindex(columns=cols_to_display, fill_value='غير متوفر')

                st.dataframe(df_safe_display, use_container_width=True, height=300)

                excel_data_bytes = create_final_report_multiple(all_extracted_data)
                if excel_data_bytes:
                    st.download_button(
                        "⬇️ تحميل ملف Excel النهائي",
                        data=excel_data_bytes,
                        file_name="All_Cases_Extracted_Report.xlsx",
                        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    )

if __name__ == '__main__':
    if not GEMINI_API_KEY:
        st.error("يرجى تعيين مفتاح Gemini API في المتغير GEMINI_API_KEY داخل ملف app.py.")
    else:
        main()
