import streamlit as st
import pandas as pd
import json
import io
import base64
import time 
import re # لاستخراج وقت الانتظار من رسالة الخطأ
from google import genai
from google.genai.errors import APIError
from db import save_to_db 

# ===============================
# 1. إعدادات API والنظام
# ===============================
# تأكد من تعيين المفتاح هنا أو عبر متغيرات البيئة
GEMINI_API_KEY = "AIzaSyA5ChIhrl9Tlob2NXyUwcau5vK75sIj-gI" 
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
MAX_RETRIES = 5 # زيادة عدد المحاولات للتعامل مع أخطاء الشبكة

# 🔥 التعليمات الجديدة والمُحسّنة للتقسيم: التركيز على تحديد كل 'تقرير قضية' كوحدة منفصلة 🔥
SEGMENTATION_PROMPT = (
    "أنت محلل وثائق آلي متخصص. مهمتك هي قراءة النص المستخرج من وثيقة رسمية كبيرة تحتوي على عدة تقارير قضايا مالية متسلسلة."
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
# 2. وظيفة إعادة المحاولة المعززة
# ===============================

def get_retry_delay_from_error(e):
    """يستخرج قيمة التأخير المطلوبة من رسالة خطأ 429."""
    try:
        # البحث عن جزء retryDelay في رسالة الخطأ (والتي تكون عادةً في صيغة JSON)
        if isinstance(e, APIError) and hasattr(e, 'message'):
            # محاولة استخراج التأخير مباشرة من نص الخطأ
            match = re.search(r'Please retry in (\d+\.?\d*)s', e.message)
            if match:
                return float(match.group(1))
            
            # محاولة التحليل من JSON
            error_data = json.loads(e.message)
            for detail in error_data.get('error', {}).get('details', []):
                if detail.get('@type') == 'type.googleapis.com/google.rpc.RetryInfo' and 'retryDelay' in detail:
                    # شكل '38s' مثلاً
                    delay_str = detail['retryDelay'].replace('s', '')
                    return float(delay_str)
        
        # إذا لم يتم العثور على تأخير محدد، نعود بـ 0
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
            st.error(f"❌ خطأ API في المحاولة {attempt + 1}: {e.message}")
            
            # 1. التعامل مع خطأ تجاوز الحصة (429)
            if e.status_code == 429:
                delay = get_retry_delay_from_error(e)
                if delay > 0:
                    st.warning(f"⚠️ تجاوز الحصة (429). سيتم الانتظار {int(delay)} ثانية بناءً على طلب الخادم...")
                    time.sleep(delay)
                    continue # إعادة المحاولة مباشرة بعد الانتظار
                else:
                    # إذا لم يتم استخراج تأخير، نستخدم الانتظار الأُسّي
                    pass 

            # 2. التعامل مع أخطاء API الأخرى والانتظار الأُسّي
            if attempt < MAX_RETRIES - 1:
                wait_time = 2 ** attempt
                st.warning(f"⚠️ خطأ API غير 429. إعادة المحاولة بعد {wait_time} ثانية...")
                time.sleep(wait_time)
            else:
                raise e # إذا انتهت المحاولات نرفع الخطأ النهائي
        
        except json.JSONDecodeError as e:
            st.error(f"❌ فشل تحليل JSON: {e}")
            raise e
        
        except Exception as e:
            st.error(f"❌ خطأ عام غير متوقع: {e}")
            raise e
    
    return None # إذا فشلت جميع المحاولات

# ===============================
# 3. وظائف المعالجة المحدثة
# ===============================

def segment_document_by_cases(file_bytes, file_name):
    """
    يستخدم Gemini لتقسيم ملف كبير متعدد القضايا إلى قائمة من القضايا الفردية (نصوص).
    """
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # إعداد محتويات الطلب (نص التعليمات + الملف كـ Base64)
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
        """الدالة التي سنحاول تنفيذها وتطبيق إعادة المحاولة عليها"""
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
            st.warning(f"⚠️ فشل التقسيم التلقائي بعد {MAX_RETRIES} محاولات. سيتم التعامل مع الملف بالكامل كقضية واحدة.")
            return [file_bytes]
            
    except Exception as e:
        st.error(f"❌ خطأ نهائي أثناء تقسيم الوثيقة: {e}")
        return [file_bytes]

def extract_financial_data(case_text_or_bytes, case_name, file_type, is_segment=False):
    """
    يقوم باستخلاص البيانات من نص قضية منفردة أو ملف.
    """
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # إعداد محتويات الطلب بناءً على نوع المدخلات
    if is_segment:
        content_parts = [
            "استخرج البيانات المطلوبة بدقة من النص المرفق. النص يمثل قضية واحدة كاملة.",
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

    def api_call():
        """الدالة التي سنحاول تنفيذها وتطبيق إعادة المحاولة عليها"""
        with st.spinner(f"⏳ جاري استخلاص معلومات القضية: '{case_name}'..."):
            response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)
        
        if not response.text:
            raise ValueError("النموذج لم يعد بنص JSON.")
            
        extracted_data = json.loads(response.text)
        
        # إضافة بيانات التتبع عند النجاح
        extracted_data['اسم الملف'] = case_name
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success(f"✅ تم استخلاص معلومات '{case_name}' بنجاح!")
        return extracted_data

    try:
        return retry_api_call(api_call)
    except Exception as e:
        st.error(f"❌ فشل الاستخلاص النهائي للقضية '{case_name}': {e}")
        # إذا فشلت جميع المحاولات، نعود ببيانات الخطأ
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
# 4. واجهة المستخدم 
# ===============================
def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")
    st.title("أداة استخلاص وتقارير القضايا")
    st.markdown("---")

    uploaded_files = st.file_uploader(
        "قم بتحميل الملفات (يمكن اختيار ملف واحد يحتوي على عدة قضايا)",
        type=["pdf","png","jpg","jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        all_extracted_data = []

        if st.button("بدء الاستخلاص والتحويل إلى Excel"):
            
            # عرض تحذير صريح بخصوص حدود API
            st.warning(
                "⚠️ **تنبيه حدود API:** هذا التطبيق يستخدم حساب API مجاني محدود بـ 10 طلبات في الدقيقة. "
                "إذا كان الملف يحتوي على عدد كبير من القضايا، سيقوم التطبيق بالانتظار (قد تصل المدة إلى دقيقة) "
                "بشكل آلي بين كل قضية لتجنب خطأ تجاوز الحصة (429)."
            )

            for uploaded_file in uploaded_files:
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()
                st.info(f"جاري معالجة الملف الأساسي: **{file_name}**")

                if file_type == 'pdf' or file_type in ['png', 'jpg', 'jpeg']:
                    
                    case_segments_or_bytes = segment_document_by_cases(file_bytes, file_name)
                    is_segment_mode = all(isinstance(item, str) for item in case_segments_or_bytes)
                    
                    if is_segment_mode and len(case_segments_or_bytes) > 0:
                        # وضع التقسيم
                        st.subheader(f"تم العثور على {len(case_segments_or_bytes)} قضية في الملف.")
                        
                        # تنفيذ عملية الاستخلاص لكل قضية
                        for i, case_content in enumerate(case_segments_or_bytes):
                            # إضافة فاصل زمني إجباري بين الطلبات لتجنب تجاوز الحصة (429)
                            if i > 0:
                                st.text("--- فاصل إجباري بين القضايا لتجنب تجاوز الحصة (429) ---")
                                time.sleep(5) # انتظار 5 ثواني كحد أدنى بين الطلبات

                            case_name = f"{file_name} (قضية #{i+1})"
                            data = extract_financial_data(case_content, case_name, file_type, is_segment=True)
                            
                            if data and 'خطأ في الاستخلاص' not in data.get('رقم الصادر', ''):
                                all_extracted_data.append(data)
                                save_to_db(data)
                            elif data:
                                all_extracted_data.append(data)
                                st.error(f"❌ فشل استخلاص بيانات القضية #{i+1} وسيتم تسجيلها كـ 'خطأ في الاستخلاص'.")

                    else:
                        # وضع القضية الواحدة (الملف بالكامل)
                        st.warning(f"تم التعامل مع '{file_name}' كقضية واحدة (فشل التقسيم). جاري الاستخلاص...")
                        data = extract_financial_data(file_bytes, file_name, file_type, is_segment=False)
                        if data:
                            all_extracted_data.append(data)
                            if 'خطأ في الاستخلاص' not in data.get('رقم الصادر', ''):
                                save_to_db(data)
                
                else:
                    st.error(f"نوع الملف {file_type} غير مدعوم للمعالجة.")


            if all_extracted_data:
                st.subheader("✅ جميع البيانات المستخلصة")
                df_display = pd.DataFrame(all_extracted_data)
                
                # إضافة عمود التسلسل (#) لغرض العرض في الجدول
                df_display.insert(0, '#', range(1, 1 + len(df_display)))

                # عرض جميع الحقول المستخلصة
                full_columns_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
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
