import streamlit as st
import pandas as pd
import json
import io
import base64
import time 
import re 
from google import genai
from google.genai.errors import APIError
# افتراض أن save_to_db موجودة في ملف db.py
from db import save_to_db 

# ===============================
# 1. إعدادات API والنظام
# ===============================
# تأكد من تعيين المفتاح هنا أو عبر متغيرات البيئة. يُفضل استخدام st.secrets
GEMINI_API_KEY = "AIzaSyA5ChIhrl9Tlob2NXyUwcau5vK75sIj-gI"
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
MAX_RETRIES = 5 

SEGMENTATION_PROMPT = (
    "أنت محلل وثائق آلي متخصص. مهمتك هي قراءة النص المستخرج من وثيقة رسمية كبيرة تحتوي على عدة تقارير قضايا مالية متسلسلة."
    "القاعدة لتقسيم النص هي: **يجب تحديد وفصل كل تقرير قضية (Case Report) عن التالي.** "
    "كل تقرير قضية يبدأ عادةً بـ 'بسم الله الرحمن الرحيم' ويتبعه العناوين الرسمية (مثل 'المملكة العربية السعودية' و 'رئاسة أمن الدولة' أو 'وزارة التجارة') وينتهي قبل بداية القضية التالية أو نهاية الوثيقة. "
    "مهمتك هي تقسيم النص إلى قائمة JSON من القضايا الفردية (segments)، حيث يمثل كل عنصر النص الكامل للقضية الواحدة. لا تقم بأي تغيير أو تلخيص للنص."
)

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
# 2. وظيفة إعادة المحاولة المعززة والتعامل مع الأخطاء
# ===============================
def get_retry_delay_from_error(e):
    """يستخرج قيمة التأخير المطلوبة من رسالة خطأ 429."""
    try:
        if isinstance(e, APIError) and hasattr(e, 'message'):
            match = re.search(r'Please retry in (\d+\.?\d*)s', e.message)
            if match: return float(match.group(1))
            
            error_data = json.loads(e.message)
            for detail in error_data.get('error', {}).get('details', []):
                if detail.get('@type') == 'type.googleapis.com/google.rpc.RetryInfo' and 'retryDelay' in detail:
                    delay_str = detail['retryDelay'].replace('s', '')
                    return float(delay_str)
        return 0 
    except Exception:
        return 0

def retry_api_call(func, *args, **kwargs):
    """
    مُغلّف (Wrapper) لتنفيذ نداءات API مع إعادة المحاولة والانتظار الأُسّي 
    والتعامل الخاص مع خطأ 429.
    """
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        
        except APIError as e:
            # st.error(f"❌ خطأ API في المحاولة {attempt + 1}: {e.message}")
            
            # 1. التعامل مع خطأ تجاوز الحصة (429) أو نموذج غير متاح (503)
            if e.status_code in [429, 503]:
                delay = 0
                if e.status_code == 429:
                    delay = get_retry_delay_from_error(e)
                
                if delay > 0:
                    st.warning(f"⚠️ تجاوز الحصة (429). سيتم الانتظار {int(delay)} ثانية بناءً على طلب الخادم...")
                    time.sleep(delay)
                    continue 
                else:
                    # نستخدم الانتظار الأُسّي لـ 503 والأخطاء الأخرى
                    pass 

            # 2. التعامل مع أخطاء API الأخرى والانتظار الأُسّي
            if attempt < MAX_RETRIES - 1:
                wait_time = 2 ** attempt
                # st.warning(f"⚠️ خطأ API {e.status_code}. إعادة المحاولة بعد {wait_time} ثانية...")
                time.sleep(wait_time)
            else:
                raise e
        
        except json.JSONDecodeError as e:
            st.error(f"❌ فشل تحليل JSON: {e}")
            raise e
        
        except Exception as e:
            st.error(f"❌ خطأ عام غير متوقع: {e}")
            raise e
    
    return None

# ===============================
# 3. وظائف المعالجة والاستخلاص
# ===============================

def segment_document_by_cases(file_bytes, file_name):
    """
    يستخدم Gemini لتقسيم ملف كبير متعدد القضايا إلى قائمة من القضايا الفردية (نصوص).
    """
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

    def api_call():
        with st.spinner(f"⏳ جاري تحليل وتقسيم القضايا في '{file_name}'..."):
            response = client.models.generate_content(
                model=MODEL_NAME, 
                contents=content_parts, 
                config=config
            )
        
        if not response.text:
            raise ValueError("النموذج لم يعد بنص JSON.")

        segment_data = json.loads(response.text)
        
        if 'cases' in segment_data and isinstance(segment_data['cases'], list) and len(segment_data['cases']) > 0:
            return segment_data['cases']
        else:
            raise ValueError("النموذج لم يتمكن من تقسيم الوثيقة بشكل صحيح أو أعاد قائمة قضايا فارغة.")

    try:
        segments = retry_api_call(api_call)
        if segments:
            st.success(f"✅ تم تقسيم '{file_name}' إلى {len(segments)} قضية بنجاح.")
            return segments
        else:
            # العودة بالملف بالكامل كقضية واحدة إذا فشل التقسيم
            return [file_bytes]
            
    except Exception as e:
        st.error(f"❌ خطأ نهائي أثناء تقسيم الوثيقة: {e}")
        return [file_bytes]

def extract_financial_data(case_content, case_name, file_type, is_segment=False):
    """
    يقوم باستخلاص البيانات من نص قضية منفردة أو ملف.
    """
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    if is_segment:
        content_parts = [
            "استخرج البيانات المطلوبة بدقة من النص المرفق. النص يمثل قضية واحدة كاملة.",
            {"text": case_content} 
        ]
    else:
        mime_type = "application/pdf" if file_type=='pdf' else f"image/{'jpeg' if file_type=='jpg' else file_type}"
        content_parts = [
            "قم باستخلاص جميع البيانات...",
            {"inlineData": {"data": base64.b64encode(case_content).decode('utf-8'), "mimeType": mime_type}}
        ]

    config = {
        "systemInstruction": SYSTEM_PROMPT,
        "responseMimeType": "application/json",
        "responseSchema": RESPONSE_SCHEMA
    }

    def api_call():
        response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)
        
        if not response.text:
            raise ValueError("النموذج لم يعد بنص JSON.")
            
        extracted_data = json.loads(response.text)
        extracted_data['اسم الملف'] = case_name
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success(f"✅ تم استخلاص معلومات '{case_name}' بنجاح!")
        return extracted_data

    try:
        return retry_api_call(api_call)
    except Exception as e:
        # st.error(f"❌ فشل الاستخلاص النهائي للقضية '{case_name}': {e}")
        return {
            'اسم الملف': case_name, 
            'وقت الاستخلاص': pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"), 
            'رقم الصادر': 'خطأ في الاستخلاص',
            'اسم المشتبه به': 'خطأ في الاستخلاص'
        }

def create_final_report_multiple(df):
    """
    يُنشئ ملف Excel من DataFrame.
    """
    import xlsxwriter
    if df.empty: return None

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # إزالة عمود التسلسل المؤقت (#) قبل التصدير
        df_export = df.drop(columns=['#'], errors='ignore')
        df_export.to_excel(writer, sheet_name='التقرير المالي', index=False)
        
        workbook, worksheet = writer.book, writer.sheets['التقرير المالي']
        worksheet.right_to_left()
        
        # تنسيق العمود الأخير (سبب الاشتباه) ليكون أوسع ويحتوي على النص كاملاً
        col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
        
        # التأكد من أن سبب الاشتباه موجود في العمود
        column_order = df_export.columns.tolist()
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
# 4. واجهة المستخدم والتعديل
# ===============================

# مفتاح الجلسة لتخزين البيانات المستخلصة بشكل دائم حتى بعد التعديل
if 'extracted_data_list' not in st.session_state:
    st.session_state['extracted_data_list'] = []

def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")
    st.title("أداة استخلاص وتقارير القضايا")
    st.markdown("---")
    
    # 1. منطقة التحميل وبدء الاستخلاص
    uploaded_files = st.file_uploader(
        "قم بتحميل الملفات (يمكن اختيار ملف واحد يحتوي على عدة قضايا)",
        type=["pdf","png","jpg","jpeg"],
        accept_multiple_files=True
    )

    if st.button("بدء الاستخلاص والتحويل إلى جدول") and uploaded_files:
        st.session_state['extracted_data_list'] = [] # تفريغ القائمة عند بدء عملية جديدة
        
        st.warning(
            "⚠️ **تنبيه حدود API:** هذا التطبيق يستخدم حساب API مجاني محدود. "
            "قد يتم تطبيق تأخيرات تلقائية لتجنب أخطاء تجاوز الحصة (429/503)."
        )

        with st.empty():
            for uploaded_file in uploaded_files:
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()
                st.info(f"جاري معالجة الملف الأساسي: **{file_name}**")

                if file_type == 'pdf':
                    
                    case_segments = segment_document_by_cases(file_bytes, file_name)
                    is_segment_mode = all(isinstance(item, str) for item in case_segments)
                    
                    # وضع التقسيم أو وضع الملف الواحد
                    for i, case_content in enumerate(case_segments):
                        if i > 0 and is_segment_mode:
                            # انتظار 5 ثواني كحد أدنى بين الطلبات لتجنب تجاوز الحصة
                            st.text(f"--- انتظار 5 ثوانٍ قبل القضية #{i+1} ---")
                            time.sleep(5) 

                        case_name = f"{file_name} (قضية #{i+1})"
                        # إذا كان المحتوى نص (segment) أو بايت (ملف واحد)
                        data = extract_financial_data(case_content, case_name, file_type, is_segment=is_segment_mode)
                        
                        if data and data.get('رقم الصادر', '') != 'خطأ في الاستخلاص':
                            st.session_state['extracted_data_list'].append(data)
                        elif data:
                            st.session_state['extracted_data_list'].append(data)
                            st.error(f"❌ فشل استخلاص بيانات القضية #{i+1} وسيتم تسجيلها كـ 'خطأ في الاستخلاص'.")

                else:
                    # معالجة ملفات الصور
                    data = extract_financial_data(file_bytes, file_name, file_type, is_segment=False)
                    if data:
                        st.session_state['extracted_data_list'].append(data)
                        
    # 2. منطقة عرض وتحرير البيانات
    
    if st.session_state['extracted_data_list']:
        st.subheader("✅ جميع البيانات المستخلصة - **قابلة للتحرير**")
        
        # تجهيز DataFrame للعرض والتحرير
        df_original = pd.DataFrame(st.session_state['extracted_data_list'])
        
        # إضافة عمود التسلسل (#) لغرض العرض والتحرير
        df_original.insert(0, '#', range(1, 1 + len(df_original)))

        # ضمان وجود جميع الأعمدة المطلوبة في الترتيب الصحيح
        column_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
        df_safe_display = df_original.reindex(columns=column_order, fill_value='غير متوفر')

        # عرض الجدول القابل للتحرير (st.data_editor)
        # استخدام st.data_editor بدلاً من st.dataframe
        edited_df = st.data_editor(
            df_safe_display,
            key="data_editor_key",
            use_container_width=True,
            height=300,
            column_config={
                '#': st.column_config.NumberColumn(
                    '#', help='رقم تسلسلي', disabled=True
                ),
                'اسم الملف': st.column_config.TextColumn(
                    'اسم الملف', help='اسم الملف/القضية المصدر', disabled=True
                ),
                'سبب الاشتباه': st.column_config.TextColumn(
                    'سبب الاشتباه', help='وصف سبب الاشتباه', width='large'
                )
            }
        )
        
        st.markdown("---")

        # 3. زر تأكيد الحفظ والتصدير
        col1, col2, col3 = st.columns([1, 1, 4])
        
        # زر تأكيد الحفظ إلى قاعدة البيانات
        if col1.button("💾 تأكيد وحفظ إلى قاعدة البيانات"):
            
            # تحويل DataFrame المحرر إلى قائمة قواميس (Records)
            edited_records = edited_df.drop(columns=['#']).to_dict('records')
            
            # حفظ كل سجل مُعدّل إلى قاعدة البيانات (المرجح هنا Firestore)
            with st.spinner("⏳ جاري حفظ البيانات المُعدلة إلى قاعدة البيانات..."):
                saved_count = 0
                for record in edited_records:
                    if record.get('رقم الصادر', '') != 'خطأ في الاستخلاص':
                        # نرسل فقط الحقول الأساسية المطلوبة للحفظ
                        save_to_db(record)
                        saved_count += 1
                    else:
                         st.warning(f"تم تخطي السجل الذي يحتوي على 'خطأ في الاستخلاص'.")

                st.success(f"✅ تم حفظ وتحديث {saved_count} سجل بنجاح في قاعدة البيانات.")
                # تحديث حالة الجلسة بالبيانات الجديدة بعد الحفظ
                st.session_state['extracted_data_list'] = edited_records
        
        # زر تصدير Excel
        excel_data_bytes = create_final_report_multiple(edited_df)
        if excel_data_bytes:
            col2.download_button(
                "⬇️ تحميل ملف Excel (المُعدّل)",
                data=excel_data_bytes,
                file_name="Edited_Cases_Report.xlsx",
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )


if __name__ == '__main__':
    # تأكد من أنك قمت بتعيين مفتاح API لـ Gemini
    if not GEMINI_API_KEY:
        st.error("يرجى تعيين مفتاح Gemini API في المتغير GEMINI_API_KEY داخل ملف app.py.")
    else:
        main()
