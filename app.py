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

# 🔥 التعليمات الجديدة والمُحسّنة للتقسيم: التركيز على تحديد كل 'تقرير قضية' كوحدة منفصلة 🔥
SEGMENTATION_PROMPT = (
    "أنت محلل وثائق آلي متخصص. تم تزويدك بالنص الكامل لوثيقة رسمية كبيرة تحتوي على عدة تقارير قضايا مالية متسلسلة."
    "القاعدة لتقسيم النص هي: **يجب تحديد وفصل كل تقرير قضية (Case Report) عن التالي.** "
    "كل تقرير قضية يبدأ عادةً بـ 'بسم الله الرحمن الرحيم' ويتبعه العناوين الرسمية (مثل 'المملكة العربية السعودية' و 'رئاسة أمن الدولة' أو 'وزارة التجارة') وينتهي قبل بداية القضية التالية أو نهاية الوثيقة. "
    "مهمتك هي تقسيم النص إلى قائمة JSON من القضايا الفردية (segments)، حيث يمثل كل عنصر النص الكامل للقضية الواحدة. لا تقم بأي تغيير أو تلخيص للنص."
)

# البرومت المستخدم للاستخلاص (Extraction Prompt) 
SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (OCR/NLP). مهمتك هي قراءة النص المستخرج من الوثيقة المالية "
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
    """
    يستخدم Gemini لتقسيم ملف كبير متعدد القضايا إلى قائمة من القضايا الفردية (نصوص).
    """
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # لتقسيم PDF، يجب إرساله كـ Base64
        content_parts = [
            SEGMENTATION_PROMPT,
            {"inlineData": {"data": base64.b64encode(file_bytes).decode('utf-8'), "mimeType": "application/pdf"}}
        ]
        
        config = {
            "systemInstruction": SEGMENTATION_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": SEGMENTATION_SCHEMA
        }

        # استخدام عدد محدود من المحاولات مع التوقف الأسي (Exponential Backoff)
        MAX_RETRIES = 5
        for attempt in range(MAX_RETRIES):
            try:
                with st.spinner(f"⏳ جاري تحليل وتقسيم القضايا في '{file_name}' (محاولة {attempt + 1}/{MAX_RETRIES})..."):
                    response = client.models.generate_content(
                        model=MODEL_NAME, 
                        contents=content_parts, 
                        config=config
                    )
                
                segment_data = json.loads(response.text)
                
                if 'cases' in segment_data and isinstance(segment_data['cases'], list) and len(segment_data['cases']) > 0:
                    st.success(f"✅ تم تقسيم '{file_name}' إلى {len(segment_data['cases'])} قضية بنجاح.")
                    return segment_data['cases']
                else:
                    # قد يعود النموذج بـ cases=[] إذا لم يجد شيئًا، نرفع خطأ للانتقال للمحاولة التالية
                    raise ValueError("النموذج لم يتمكن من تقسيم الوثيقة بشكل صحيح أو لم يجد قضايا.")
            
            except (APIError, json.JSONDecodeError, ValueError) as e:
                if attempt < MAX_RETRIES - 1:
                    # تجربة إعادة محاولة مع تأخير
                    import time
                    wait_time = 2 ** attempt  # 1s, 2s, 4s...
                    time.sleep(wait_time)
                else:
                    st.error(f"❌ فشل التقسيم بعد {MAX_RETRIES} محاولات: {e}")
                    # إذا فشلت جميع المحاولات، نعود للمسار القديم (قضية واحدة)
                    break 
        
        # إذا لم يتم العثور على تقسيم، نعود للمسار القديم
        st.warning(f"⚠️ فشل التقسيم التلقائي. سيتم التعامل مع الملف بالكامل كقضية واحدة.")
        return [file_bytes] 
            
    except Exception as e:
        st.error(f"❌ خطأ غير متوقع أثناء تقسيم الوثيقة: {e}")
        return [file_bytes]

def extract_financial_data(case_text_or_bytes, case_name, file_type, is_segment=False):
    """
    يقوم باستخلاص البيانات من نص قضية منفردة أو ملف (كما كان سابقاً).
    **تم تبسيط هذا القسم لضمان حل مشاكل الـ Indentation**
    """
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # إعداد محتويات الطلب
    if is_segment:
        # إذا كانت المدخلات نصاً مقسماً، نرسل النص ونشير إلى أنه نص عادي
        content_parts = [
            "استخرج البيانات المطلوبة بدقة من النص المرفق. النص يمثل قضية واحدة كاملة.",
            {"text": case_text_or_bytes} 
        ]
    else:
        # إذا كانت المدخلات بايتات (ملف)، نرسلها كـ inlineData
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

    # استخدام عدد محدود من المحاولات مع التوقف الأسي (Exponential Backoff)
    MAX_RETRIES = 5
    for attempt in range(MAX_RETRIES):
        try:
            with st.spinner(f"⏳ جاري استخلاص معلومات القضية: '{case_name}' (محاولة {attempt + 1}/{MAX_RETRIES})..."):
                response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)

            extracted_data = json.loads(response.text)
            
            # إضافة بيانات التتبع
            extracted_data['اسم الملف'] = case_name
            extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            st.success(f"✅ تم استخلاص معلومات '{case_name}' بنجاح!")
            return extracted_data # العودة بالبيانات المستخلصة بنجاح
        
        except (APIError, json.JSONDecodeError, Exception) as e:
            if attempt < MAX_RETRIES - 1:
                import time
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            else:
                st.error(f"❌ فشل الاستخلاص بعد {MAX_RETRIES} محاولات: {e}")
                # إذا فشلت جميع المحاولات، ستتابع الدالة للعودة ببيانات الخطأ

    # إذا انتهت حلقة المحاولات دون نجاح، نعود ببيانات الخطأ
    return {
        'اسم الملف': case_name, 
        'وقت الاستخلاص': pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"), 
        'رقم الصادر': 'خطأ في الاستخلاص',
        'اسم المشتبه به': 'خطأ في الاستخلاص'
    }

def create_final_report_multiple(all_data):
    """
    يجمع البيانات المستخلصة ويُنشئ ملف Excel.
    """
    import xlsxwriter
    if not all_data: return None

    df_list = []
    for i, data in enumerate(all_data, 1):
        # إضافة رقم التسلسل هنا لتقرير Excel
        data_copy = data.copy()
        data_copy['#'] = i
        df_list.append(data_copy)

    df = pd.DataFrame(df_list)
    
    # ضمان وجود جميع الأعمدة المطلوبة في الترتيب الصحيح
    column_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
    
    # إعادة ترتيب الأعمدة وتعبئة القيم المفقودة بـ 'غير متوفر'
    df = df.reindex(columns=column_order, fill_value='غير متوفر')

    output = io.BytesIO()
    # استخدام with للتعامل مع Writer بشكل آمن
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='التقرير المالي', index=False)
        workbook, worksheet = writer.book, writer.sheets['التقرير المالي']
        worksheet.right_to_left()
        
        # تنسيق العمود الأخير (سبب الاشتباه) ليكون أوسع ويحتوي على النص كاملاً
        col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
        # التأكد من أن سبب الاشتباه موجود في العمود
        if 'سبب الاشتباه' in column_order:
             worksheet.set_column(column_order.index('سبب الاشتباه'), column_order.index('سبب الاشتباه'), 120, col_format)
        
        # تنسيق الأعمدة الأخرى
        for i, col_name in enumerate(column_order):
            if col_name != 'سبب الاشتباه':
                width = 25 if col_name in ["اسم المشتبه به","رقم صاحب العمل/ السجل التجاري"] else 18
                worksheet.set_column(i,i,width,col_format)
    
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

                # الميزة الجديدة: تقسيم الملف الكبير إلى قضايا منفردة 
                if file_type == 'pdf' or file_type in ['png', 'jpg', 'jpeg']:
                    
                    # نستخدم segment_document_by_cases وهي سترجع قائمة من النصوص (segments) أو قائمة تحتوي على البايتات الأصلية إذا فشل التقسيم
                    case_segments_or_bytes = segment_document_by_cases(file_bytes, file_name)
                    
                    # تحديد ما إذا كان الناتج عبارة عن نصوص مقسمة (is_segment=True) أم بايتات أصلية (is_segment=False)
                    is_segment_mode = all(isinstance(item, str) for item in case_segments_or_bytes)
                    
                    if is_segment_mode and len(case_segments_or_bytes) > 0:
                        # وضع التقسيم
                        st.subheader(f"تم العثور على {len(case_segments_or_bytes)} قضية في الملف.")
                        for i, case_content in enumerate(case_segments_or_bytes):
                            case_name = f"{file_name} (قضية #{i+1})"
                            # نرسل النص المستخرج للقضية الواحدة لعملية الاستخلاص
                            data = extract_financial_data(case_content, case_name, file_type, is_segment=True)
                            if data:
                                all_extracted_data.append(data)
                                save_to_db(data)
                    else:
                        # وضع القضية الواحدة (الملف بالكامل)
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
                
                # إضافة عمود التسلسل (#) لغرض العرض في الجدول
                df_display.insert(0, '#', range(1, 1 + len(df_display)))

                # 🛑 عرض جميع الحقول المستخلصة
                full_columns_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
                
                # ضمان وجود الأعمدة المطلوبة قبل العرض
                df_safe_display = df_display.reindex(columns=full_columns_order, fill_value='غير متوفر')

                st.dataframe(df_safe_display, use_container_width=True, height=500)

                excel_data_bytes = create_final_report_multiple(all_extracted_data)
                if excel_data_bytes:
                    st.download_button(
                        "⬇️ تحميل ملف Excel النهائي",
                        data=excel_data_bytes,
                        file_name="All_Cases_Extracted_Report.xlsx",
                        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    )

if __name__ == '__main__':
    # تأكد من أنك قمت بتعيين مفتاح API لـ Gemini
    if not GEMINI_API_KEY:
        st.error("يرجى تعيين مفتاح Gemini API في المتغير GEMINI_API_KEY داخل ملف app.py.")
    else:
        main()
