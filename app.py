# app.py 

import streamlit as st
import pandas as pd
import json
import io
import base64
import os
import re
import pytz
import time
import concurrent.futures
from dotenv import load_dotenv

# استيراد مكتبات Gemini
from google import genai
from google.genai.errors import APIError as GeminiAPIError

# محاولة استيراد الدوال من db.py
try:
    from db import save_to_db, fetch_all_reports, initialize_db
except ImportError:
    st.error("❌ فشل استيراد db.py. تأكد من وجود الملف وأن الدوال (save_to_db, fetch_all_reports, initialize_db) معرفة فيه.")
    # تعريف الدوال فارغة لتجنب الانهيار إذا كان الملف مفقودًا
    def save_to_db(*args): st.error("❌ DB function missing.")
    def fetch_all_reports(): return None, None
    def initialize_db(): pass

# ===============================
# 1. إعدادات API (محدثة لـ Gemini API)
# ===============================
load_dotenv()


MODEL_NAME = os.getenv("MODEL_NAME", 'gemini-2.5-flash')

# تهيئة العميل 
try:
    client = genai.Client()
except Exception as e:
    st.error(f"❌ خطأ في تهيئة Gemini Client: {e}")
    client = None

# ===============================
# 2. حقول التقرير والمخطط (تم التعديل لإضافة النص الخام)
# ===============================
REPORT_FIELDS_ARABIC = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة",
    "رقم الدلالة",
    "النص الخام المستخرج" # <<< حقل جديد للتحقق من قراءة OCR
]

DELALAT_MAPPING = {
    1: "تكرار العمليات المالية (إيداعات، حوالات سحوبات مشتريات) في حساب المقيم من جهة عمله أو من أفراد أو كيانات تجارية غير مرتبطين بجهة العمل بشكل شبه يومي لا تتناسب مع دخله السنوي (مع مراعاة نمط العمليات المدينة من الحساب).",
    2: "تحويلات أو إيداعات نقدية من حساب عميل مقيم الى حساب فرد سعودي أو كيان تجاري.",
    3: "حوالات صادرة أو عمليات مالية متنوعة من حساب مقيم أجنبي لعمليات سداد مصروفات على سبيل المثال (سداد إيجارات - فواتير - رسوم - غرامات - شراء سلع بمبالغ عالية) تنم عن المتاجرة فيها أو إعادة بيعها.",
    4: "حوالات دولية صادرة من حساب فرد سعودي أو حساب كيان تجاري إلى حسابات أشخاص بشكل متكرر لا تربطهم به غرض أو علاقة عمل.",
    5: "مقيم يقوم بتنفيذ عمليات تحويل مالية خارج المملكة له أو لأشخاص آخرين بمبالغ لا تتناسب مع دخله وقد يكون مصدرها إيداعات نقدية من عدة عملاء مقيمين.",
    6: "حوالات دولية واردة للحساب الشخصي للمقيم أو للبطاقات الائتمانية بمبالغ عالية تنم عن إدارة نشاط تجاري داخل المملكة.",
    7: "شخص مقيم يقوم بتنفيذ عمليات مالية (إيداع شيك أو صرف شيك أو استقبال حواله مالية) وليس لديه حساب بنكي (عميل عابر).",
    8: "إيداعات نقدية في حساب كيان تجاري بشكل متكرر أو إيداعات مبيعات نقاط بيع، يليها تنفيذ حوالات خارجية أو حوالات داخلية لعدة عملاء مقيمين أو عمليات سحب من قبل صاحب الكيان أو المفوض على الحساب سواءً سحب نقدي أو صرف شيكات من المبالغ المودعة (مع الأخذ في الاعتبار طبيعة نشاط الكيان التجاري).",
    9: "حوالات دولية واردة أو صادرة لحساب الكيان التجاري لا تتناسب مع نشاط الكيان التجاري.",
    10: "تفويض أجنبي على حساب بنكي عائد لكيان تجاري وتمكينه من الحساب بشكل كامل وحضوره معه لفرع البنك بشكل دائم وتحرير شيكات له دون وجود مبرر أو غرض واضح.",
    11: "فتح عدة حسابات الفروع كيان تجاري لنفس النشاط دون وجود ارتباط واضح بين هذه الحسابات، نظراً لإدارة الحساب الخاص بالفرع من قبل المقيم."
}

delalat_list = "\n".join([f"    - {k}: {v}" for k, v in DELALAT_MAPPING.items()])

# تعريف مجموعات الدلالات للتحليل القسري
D_ENT = {8, 9, 10, 11}    # كيان تجاري - يُمنع للأفراد
D_IND = {1, 3, 5, 6, 7}     # فرد/مقيم - يُمنع للكيانات
D_COMMON = {2, 4}           # دلالات مشتركة

# <<< تم تحديث SYSTEM_PROMPT لزيادة المرونة وطلب النص الخام >>>
SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (Gemini API). مهمتك هي قراءة الوثيقة المرفقة (PDF/صورة) "
    "واستخلاص جميع البيانات وتحويلها إلى كائن JSON وفقاً للحقول المطلوبة. "
    "يجب تحويل جميع التواريخ إلى صيغة رقمية موحدة 'YYYY/MM/DD'. "
    "استخدم 'غير متوفر' للحقول المفقودة. كن مرناً في مطابقة الحقول، وابحث عن المصطلحات القريبة أو الشبيهة. "
    
    "**تعليمات تحديد 'رقم الدلالة':** "
    
    "بعد استخلاص النص كاملاً في حقل **'سبب الاشتباه'**، قم بتحليل هذا النص مباشرةً "
    "واختر رقم الدلالة الأنسب من القائمة أدناه. إذا انطبق أكثر من رقم، ضعهما مفصولين بفاصلة فقط (مثال: 1,5). لاحظ أن القيمة المستخلصة هي **قيمة مقترحة** وسيتم تطبيق عليها منطق قسري لاحقاً في الكود.\n\n"
    
    "**قائمة الدلالات:**\n"
    "1: تكرار العمليات المالية (إيداعات، حوالات سحوبات مشتريات) في حساب المقيم لا تتناسب مع دخله السنوي. \n"
    "2: تحويلات أو إيداعات نقدية من حساب عميل مقيم الى حساب فرد سعودي أو كيان تجاري. \n"
    "3: حوالات صادرة أو عمليات مالية متنوعة من حساب مقيم أجنبي لعمليات سداد مصروفات تنم عن المتاجرة فيها أو إعادة بيعها. \n"
    "4: حوالات دولية صادرة من حساب فرد سعودي أو حساب كيان تجاري إلى حسابات أشخاص بشكل متكرر لا تربطهم به غرض أو علاقة عمل. \n"
    "5: مقيم يقوم بتنفيذ عمليات تحويل مالية خارج المملكة له أو لأشخاص آخرين بمبالغ لا تتناسب مع دخله وقد يكون مصدرها إيداعات نقدية من عدة عملاء مقيمين. \n"
    "6: حوالات دولية واردة للحساب الشخصي للمقيم أو البطاقات الائتمانية بمبالغ عالية تنم عن إدارة نشاط تجاري داخل المملكة. \n"
    "7: شخص مقيم يقوم بتنفيذ عمليات مالية (إيداع شيك أو صرف شيك أو استقبال حواله مالية) وليس لديه حساب بنكي (عميل عابر). \n"
    "8: إيداعات نقدية في حساب كيان تجاري بشكل متكرر أو إيداعات مبيعات نقاط بيع، يليها تنفيذ حوالات خارجية أو داخلية لعدة عملاء مقيمين أو عمليات سحب. \n"
    "9: حوالات دولية واردة أو صادرة لحساب الكيان التجاري لا تتناسب مع نشاط الكيان التجاري. \n"
    "10: تفويض أجنبي على حساب بنكي عائد لكيان تجاري وتمكينه من الحساب بشكل كامل دون وجود مبرر أو غرض واضح. \n"
    "11: فتح عدة حسابات الفروع كيان تجاري لنفس النشاط دون وجود ارتباط واضح بين هذه الحسابات، نظراً لإدارة الحساب الخاص بالفرع من قبل المقيم. \n"
    
    "**المهمة الإضافية:** يجب عليك إرجاع النص الكامل الذي تم استخلاصه من الوثيقة في حقل إضافي يسمى 'النص الخام المستخرج'. هذا الحقل سيساعدنا في التحقق من مشكلة قراءة الملفات (OCR). " # <<< التوجيه الجديد
    
    "يجب أن تكون القيمة المستخلصة في حقل 'رقم الدلالة' هي **الرقم فقط** (مثال: 1 أو 8 أو 8,11). "
    "أجب فقط بـ JSON نظيف دون أي نص إضافي أو تنسيق Markdown (مثل ```json...```). "
)
# ===============================
# دوال مساعدة
# ===============================

def arabic_to_english_numbers(text):
    if not isinstance(text, str):
        return text
    arabic_map = {'٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
                  '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'}
    return text.translate(str.maketrans(arabic_map))

def pre_process_data_fix_dates(data):
    start_key = "تاريخ الدارسة من"
    end_key = "تاريخ الدراسة الى"
    start_date_value = data.get(start_key, "")
    if start_date_value:
        clean_value = re.sub(r'[^\d]', '', start_date_value).strip()
        if len(clean_value) == 16:
            date1_clean = clean_value[:8]
            date2_clean = clean_value[8:]
            date1_formatted = f"{date1_clean[:4]}/{date1_clean[4:6]}/{date1_clean[6:]}"
            date2_formatted = f"{date2_clean[:4]}/{date2_clean[4:6]}/{date2_clean[6:]}"
            data[start_key] = date1_formatted
            if not data.get(end_key) or data.get(end_key).strip() in ['', 'غير متوفر']:
                data[end_key] = date2_formatted
    return data

def check_for_suspicion(data):
    suspicion_indicator = ""
    date_fields = ["تاريخ الصادر", "تاريخ الوارد"]
    for field in date_fields:
        date_val = data.get(field, "")
        try:
            date_str_en = arabic_to_english_numbers(str(date_val))
            parts = re.split(r'[/\-.]', date_str_en)
            year_str = parts[0]
            year = int(year_str) if year_str.isdigit() else 0
            if year > 100 and year < 1400:  
                suspicion_indicator += f"🔴 ({field}: سنة غير طبيعية) "
        except Exception:
            pass
            
    financial_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    for field in financial_fields:
        val = data.get(field, "")
        if str(val).strip() in ['0', '0.00', '٠', '٠,٠٠']:
            suspicion_indicator += f"⚠️ ({field} = 0) "
    return suspicion_indicator.strip() or "✅ سليم"

# ----------------------------------------------------
# 3. دالة تطبيق المنطق القسري الجديدة (محرك القواعد)
# ----------------------------------------------------

def apply_strict_rules(extracted_data):
    """
    تطبق قواعد المنع والإلزام القسري (المنطق الحسابي الثابت) على البيانات المستخلصة 
    لتحديد 'رقم الدلالة' بدقة 100%، متجاوزة الاستدلال اللغوي لـ Gemini.
    """
    
    # تحويل الأرقام المالية إلى قيم رقمية نظيفة لضمان التطبيق الحسابي
    def clean_number(value):
        value = arabic_to_english_numbers(str(value).replace(',', '').replace('.', '')) # إزالة الفواصل والنقاط كفواصل آلاف
        # استخراج أول مجموعة من الأرقام الصحيحة/العشرية
        match = re.search(r'(\d+)', value)
        return float(match.group(0)) if match else 0.0

    # استخلاص الحقول الأساسية
    sijil_tijari = extracted_data.get("رقم صاحب العمل/ السجل التجاري", "")
    income = clean_number(extracted_data.get("الدخل السنوي", 0))
    deposits = clean_number(extracted_data.get("إجمالي إيداع الدراسة", 0))
    suspicion_text = extracted_data.get("سبب الاشتباه", "").upper()
    
    # محاولة التحقق من الكيان بناءً على النص الخام إذا لم يكن السجل متوفراً
    raw_text = extracted_data.get("النص الخام المستخرج", "").upper()
    is_entity_keywords = ["سجل تجاري", "كيان تجاري", "تموينات", "مؤسسة", "مكتب"]
    is_entity_from_text = any(keyword in raw_text for keyword in is_entity_keywords)
    
    is_entity = (sijil_tijari not in ["غير متوفر", "غير_متوفر", None, ""]) or is_entity_from_text
    
    final_indicators = set()

    # ****************************************************
    # أ. استخلاص الدلالات المحتملة كنقطة انطلاق (من دلالات Gemini)
    # ****************************************************
    
    delala_from_gemini = extracted_data.get("رقم الدلالة", "غير متوفر")
    
    for num_str in str(delala_from_gemini).split(','):
        try:
            num = int(num_str.strip())
            # نأخذ أي دلالة استخلصها Gemini مبدئياً
            if 1 <= num <= 11:
                final_indicators.add(num)
        except ValueError:
            pass 

    # ****************************************************
    # ب. قاعدة المنع القاطع (Hard Rule Enforcement)
    # ****************************************************

    if is_entity:
        # حالة الكيان التجاري: يُمنع قسراً اختيار أي دلالة فردية (تطبيق المنع)
        final_indicators -= D_IND
    else:
        # حالة الفرد/المقيم: يُمنع قسراً اختيار أي دلالة تجارية (تطبيق المنع)
        final_indicators -= D_ENT
        
    # ****************************************************
    # ج. قواعد الإلزام الحسابي واللغوي (Compulsory Addition)
    # ****************************************************
    
    # 1. الإلزام 1 (عدم التناسب المالي) - يطبق على الأفراد
    if not is_entity and income > 0 and deposits > 0:
        ratio = deposits / income
        if ratio > 3.0: # يمكن تعديل هذا الرقم (عتبة عدم التناسب)
            final_indicators.add(1)
            
    # 2. الإلزام 3 (المتاجرة بالخدمات) - خاصة بالفرد/المقيم
    # إضافة كلمات مفتاحية للتأكد
    if not is_entity and any(keyword in suspicion_text for keyword in ["STC PAY", "سداد فواتير بكميات", "شراء سلع بمبالغ عالية", "رسوم", "غرامات", "سداد إيجارات"]):
        final_indicators.add(3)
        
    # 3. الإلزام 5 (تحويلات دولية كبيرة للمقيم)
    if not is_entity and any(keyword in suspicion_text for keyword in ["حوالات دولية صادرة", "تحويل مالية خارج المملكة", "تحويلات لا تتناسب مع الدخل"]):
        final_indicators.add(5)
        
    # 4. الإلزام 10 (تفويض أجنبي على الكيان)
    if is_entity and any(keyword in suspicion_text for keyword in ["تفويض أجنبي", "تمكينه من الحساب بشكل كامل", "تحرير شيكات له"]):
        final_indicators.add(10)
        
    # 5. الإلزام 11 (فتح عدة حسابات/ فروع)
    if is_entity and any(keyword in suspicion_text for keyword in ["فتح عدة حسابات الفروع", "إدارة الحساب الخاص بالفرع من قبل المقيم"]):
        final_indicators.add(11)
        
    # 6. الإلزام 8 (إيداعات كيان غير متناسبة) - تم الإبقاء عليه مرناً نسبياً
    if is_entity and deposits > 1000000 and any(keyword in suspicion_text for keyword in ["حوالات واردة داخلية", "مبيعات نقاط بيع", "سحب آلي"]):
        final_indicators.add(8)


    # ----------------------------------------------------
    # د. التنسيق النهائي للمُخرَج
    # ----------------------------------------------------
    
    # تحويل المجموعة (Set) إلى سلسلة نصية مفصولة بفاصلة ومرتبة
    return ",".join(map(str, sorted(list(final_indicators))))


# ===============================
# 4. دالة الاستخلاص عبر Gemini API
# ===============================
def extract_financial_data(file_bytes, file_name, file_type):
    """يستدعي Gemini API ليُرجع JSON مطابق للمخطط، ثم يطبق محرك القواعد."""
    if not client:
        return None

    MAX_RETRIES = 3
    INITIAL_WAIT_SECONDS = 5
    
    # 1. إعداد محتوى الملف 
    
    mime_type_map = {
        'pdf': "application/pdf",
        'jpg': "image/jpeg",
        'jpeg': "image/jpeg",
        'png': "image/png"
    }
    mime_type = mime_type_map.get(file_type.lower(), "application/octet-stream")

    try:
        file_part = genai.types.Part.from_bytes(
            data=file_bytes,
            mime_type=mime_type
        )
    except Exception as e:
        
        return None

    # بناء قائمة محتوى الرسالة
    content_parts = [
        f"{SYSTEM_PROMPT}",
        file_part
    ]

    for attempt in range(MAX_RETRIES):
        try:
            
            # 2. استدعاء API 
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=content_parts,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json", 
                    temperature=0.0
                )
            )

            # 3. استخراج النص 
            json_text = response.text
            
            # 4. تحليل JSON
            try:
                extracted_data = json.loads(json_text)
            except Exception as e_json:
                raise ValueError(f"فشل تحليل JSON: {e_json} - النص: {json_text[:200]}") 

            # 5. التنظيف والإضافات
            extracted_data = pre_process_data_fix_dates(extracted_data)
            extracted_data['اسم الملف'] = file_name
            
            # >>>>>> تطبيق المنطق القسري هنا <<<<<<
            
            # 1. تطبيق قواعد المنع والإلزام بواسطة الكود البرمجي (بناءً على البيانات المستخلصة)
            final_delalat = apply_strict_rules(extracted_data) 
            
            # 2. تجاوز قيمة 'رقم الدلالة' المستخلصة من Gemini بالقيمة الناتجة عن الكود
            extracted_data['رقم الدلالة'] = final_delalat
            
            # >>>>>> نهاية الإضافة الجديدة <<<<<<


            riyadh_tz = pytz.timezone('Asia/Riyadh')
            extracted_data['وقت الاستخلاص'] = pd.Timestamp.now(tz=riyadh_tz).strftime("%Y-%m-%d %H:%M:%S")
            extracted_data['مؤشر التشتت'] = check_for_suspicion(extracted_data)

            
            for fld in REPORT_FIELDS_ARABIC:
                if fld not in extracted_data:
                    extracted_data[fld] = "غير متوفر"
                    
            return extracted_data 

        except GeminiAPIError as e:
            error_message = str(e)
            is_overloaded_error = '429' in error_message or '500' in error_message
            
            if is_overloaded_error and attempt < MAX_RETRIES - 1:
                wait_time = INITIAL_WAIT_SECONDS * (2 ** attempt) 
                time.sleep(wait_time)
                continue 
            else:
                raise RuntimeError(f"خطأ API: {e}")
                
        except Exception as e:
            is_last = (attempt == MAX_RETRIES - 1)
            wait_time = INITIAL_WAIT_SECONDS * (2 ** attempt)
            if not is_last:
                time.sleep(wait_time)
                continue
            else:
                raise Exception(f"خطأ غير متوقع: {e}")
                
    return None

# ===============================
# وظائف التقرير وواجهة المستخدم (بدون تغيير)
# ===============================
def create_final_report_from_db(records, column_names):
    import xlsxwriter
    if not records:
        st.warning("لا توجد بيانات في قاعدة البيانات لتصديرها.")
        return None

    df = pd.DataFrame(records, columns=column_names)
    df.insert(0, '#', range(1, len(df) + 1))

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    sheet_name = 'التقرير المالي النهائي'
    df.to_excel(writer, sheet_name=sheet_name, index=False)

    workbook, worksheet = writer.book, writer.sheets[sheet_name]
    worksheet.right_to_left()
    col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})

    for i, col_name in enumerate(df.columns):
        if col_name in ['سبب الاشتباه', 'النص الخام المستخرج']: # <<< إضافة الحقل الجديد ليكون واسعاً في Excel
            worksheet.set_column(i, i, 120, col_format)
        else:
            width = 25 if col_name in ["اسم المشتبه به", "رقم صاحب العمل/ السجل التجاري", "اسم الملف", "وقت الاستخلاص"] else 18
            worksheet.set_column(i, i, width, col_format)

    writer.close()
    output.seek(0)
    return output.read()


def display_basic_stats():
    st.markdown("---")
    st.subheader("إحصائيات عامة 📈")
    report_data = fetch_all_reports()
    total_count = 0
    if report_data and report_data[0]:
        records, _ = report_data
        total_count = len(records)

    st.metric(label="إجمالي عدد السجلات/الملفات المحفوظة", value=total_count)
    st.markdown("---")


# ===============================
# CSS 
# ===============================
st.markdown(
    """
    <style>
    .stApp { background-color: #f5f7fa; font-family: "Tajawal", sans-serif; }
    h1,h2,h3 { color: #1a3c6e !important; font-weight: 700 !important; }
    p, div, span { font-size: 16px !important; }
    .stButton button { background-color: #1a3c6e !important; color: white !important; border-radius: 10px !important; padding: 10px 25px !important; font-size: 17px !important; transition: 0.3s; }
    .stButton button:hover { background-color: #102649 !important; transform: scale(1.05); }
    .stDataFrame table { border-radius: 10px !important; }
    .dataframe tbody tr:nth-child(odd) { background-color: #eef2f7 !important; }
    .dataframe tbody tr:hover { background-color: #d7e3ff !important; }
    </style>
    """,
    unsafe_allow_html=True
)

# ===============================
# نقطة البداية للتطبيق 
# ===============================
def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")
    st.title("📄 نظام استخلاص البيانات")
    st.markdown("---")

    # تهيئة قاعدة البيانات
    try:
        initialize_db()
    except Exception as e:
        st.error(f"❌ فشل في تهيئة قاعدة البيانات: {e}")

    if 'extracted_data_df' not in st.session_state:
        st.session_state['extracted_data_df'] = pd.DataFrame()

    uploaded_files = st.file_uploader(
        "📤 قم بتحميل الملفات (pdf, png, jpg, jpeg) - يمكنك اختيار عدة ملفات",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        
        if st.button("🚀بدء الاستخلاص"):
            total_files = len(uploaded_files)
            progress_bar = st.progress(0)
            status_text = st.empty()
            processed_count = 0
            all_extracted_data = []

        
            status_text.info(f"⏳ بدء معالجة  {total_files} .")
            
        
            tasks = []
            for uploaded_file in uploaded_files:
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()
                tasks.append((file_bytes, file_name, file_type))

        
            MAX_CONCURRENT_WORKERS = 10 
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_WORKERS, total_files)) as executor:
                
                future_to_file = {
                    executor.submit(extract_financial_data, bytes, name, type_): name
                    for bytes, name, type_ in tasks
                }
                
                # التكرار على المستقبلات المكتملة وإضافة النتائج
                for future in concurrent.futures.as_completed(future_to_file):
                    file_name = future_to_file[future]
                    try:
                        data = future.result()
                        if data:
                            all_extracted_data.append(data)
                            st.success(f"✅ تم استخلاص البيانات من **{file_name}** بنجاح.")
                        else:
                            st.warning(f"⚠️ فشل استخلاص البيانات من **{file_name}** بشكل كامل.")
                    except Exception as exc:
                        st.error(f"❌ الملف **{file_name}** أثار استثناء أثناء المعالجة: {exc}")
                    
                    processed_count += 1
                    progress_bar.progress(processed_count / total_files)
            
            # المعالجة النهائية بعد اكتمال جميع الملفات
            if all_extracted_data:
                status_text.success(f"✅ اكتمل استخلاص جميع الملفات ({len(all_extracted_data)} ملفات).")
                new_df = pd.DataFrame(all_extracted_data)
                
                # ترتيب الأعمدة للعرض مع وضع النص الخام في النهاية
                display_cols = ["مؤشر التشتت", "اسم الملف", "وقت الاستخلاص"] + [f for f in REPORT_FIELDS_ARABIC if f != "النص الخام المستخرج"] + ["النص الخام المستخرج"]
                new_df = new_df.reindex(columns=display_cols, fill_value='غير متوفر')
                st.session_state['extracted_data_df'] = pd.concat([st.session_state['extracted_data_df'], new_df], ignore_index=True)
            else:
                status_text.error("❌ فشل استخلاص أي بيانات.")
                progress_bar.empty()

    if not st.session_state['extracted_data_df'].empty:
        st.subheader("✏️ جميع البيانات المستخلصة (قابلة للتعديل)")

        if st.button("💡 استخرج نص الدلالة المطابقة"):
            temp_df = st.session_state['extracted_data_df'].copy()
            if 'نص الدلالة المطابقة (للمراجعة)' in temp_df.columns:
                temp_df.drop(columns=['نص الدلالة المطابقة (للمراجعة)'], inplace=True, errors='ignore')

            def get_delala_description(row):
                delala_num_str = str(row.get('رقم الدلالة', 'غير متوفر')).strip()
                descriptions = []
                # معالجة الأرقام المتعددة المفصولة بفاصلة
                for num_item in delala_num_str.split(','):
                    try:
                        num = int(num_item.strip())
                        descriptions.append(f"({num}) {DELALAT_MAPPING.get(num, 'رقم الدلالة المستخلصة غير صحيح')}")
                    except ValueError:
                        descriptions.append(f"(غير صحيح) {num_item.strip()}")
                return "\n\n".join(descriptions)

            if 'رقم الدلالة' in temp_df.columns:
                temp_df.insert(temp_df.columns.get_loc('رقم الدلالة') + 1,
                                'نص الدلالة المطابقة (للمراجعة)',
                                temp_df.apply(get_delala_description, axis=1))
            st.session_state['extracted_data_df'] = temp_df
            st.rerun()

        edited_df = st.data_editor(
            st.session_state['extracted_data_df'],
            use_container_width=True,
            num_rows="dynamic"
        )
        

        st.markdown("---")
        if st.button("💾 تأكيد وحفظ التعديلات في قاعدة البيانات"):
            saved_count = 0
            total_rows = len(edited_df)
            status_placeholder = st.empty()
            
            # الأعمدة التي يجب حذفها قبل الحفظ في قاعدة البيانات
            cols_to_drop = ['مؤشر التشتت', 'نص الدلالة المطابقة (للمراجعة)', 'النص الخام المستخرج'] 
            
            for index, row in edited_df.iterrows():
                row_data = dict(row)
                # حذف الأعمدة المؤقتة قبل الحفظ
                for col in cols_to_drop:
                    row_data.pop(col, None)
                    
                if save_to_db(row_data):
                    saved_count += 1
                else:
                    status_placeholder.error(f"❌ فشل حفظ السجل رقم {index + 1}.")
                    break

            if saved_count == total_rows:
                status_placeholder.success(f"✅ تم حفظ {saved_count} سجل بنجاح!")
                st.session_state['extracted_data_df'] = pd.DataFrame()
                st.rerun()
            elif saved_count > 0:
                status_placeholder.warning(f"⚠️ تم حفظ {saved_count} فقط. راجع الأخطاء.")
            else:
                status_placeholder.error("❌ فشل حفظ جميع السجلات.")

    # إحصائيات وتصدير
    display_basic_stats()

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
