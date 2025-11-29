import streamlit as st
import pandas as pd
from google import genai
from google.genai import types
from google.genai.errors import APIError
from arabic_reshaper import reshape
from bidi.algorithm import get_display
import os
import json
import io
import time 
# 🚨 هذه هي المكتبات التي تسببت في الخطأ، والتي تم الآن إضافتها لملف requirements.txt
from firebase_admin import initialize_app, firestore, credentials
from google.cloud.exceptions import NotFound

# ----------------------------------------------------------------
# 1. إعدادات API والنصوص العربية وتهيئة Firebase
# ----------------------------------------------------------------

# 🚨 هام: قم بتعيين مفتاح API الخاص بكِ هنا!
GEMINI_API_KEY = "AIzaSyA3jr9tbNVYIbpV1yOQtg5dxS3lIuGtMag" # يرجى لصق المفتاح الجديد الصالح هنا!

# تهيئة Gemini Client
client = None
try:
    if GEMINI_API_KEY:
        client = genai.Client(api_key=GEMINI_API_KEY)
        os.environ['GEMINI_API_KEY'] = GEMINI_API_KEY
    else:
         client = genai.Client()
except Exception as e:
    error_message = f"فشل في تهيئة عميل Gemini: {e}"
    st.error(get_display(reshape(error_message)))

if client is None:
    st.error(get_display(reshape("❌ فشل في تهيئة عميل Gemini. تأكدي من توفير مفتاح API صالح.")))

# دالة تصحيح النص العربي
def fix_arabic(text):
    """يعالج النصوص العربية لضمان العرض الصحيح (من اليمين لليسار)."""
    if isinstance(text, str) and text:
        reshaped_text = reshape(text)
        return get_display(reshaped_text)
    return text

# -----------------------------------------------------
# 🚀 1.1 تهيئة Firebase Firestore للتخزين الدائم
# -----------------------------------------------------

# تهيئة Firebase باستخدام متغيرات البيئة في Canvas
if 'db' not in st.session_state:
    try:
        # قراءة متغيرات البيئة (متاحة في Canvas)
        # هذا يضمن أن يتم الاتصال بـ Firebase التي يوفرها النظام الأساسي تلقائياً.
        FIREBASE_CONFIG = json.loads(os.environ.get('__firebase_config', '{}'))
        APP_ID = os.environ.get('__app_id', 'default-app-id')
        
        # التأكد من وجود البيانات الأساسية للتهيئة
        if FIREBASE_CONFIG and APP_ID:
            
            # محاولة تهيئة التطبيق مرة واحدة فقط
            # get_app() تفشل إذا لم يتم التهيئة بعد، initialize_app() تهيئ.
            try:
                from firebase_admin import get_app
                get_app()
            except ValueError:
                cred = credentials.Certificate(FIREBASE_CONFIG)
                initialize_app(cred)
                 
            st.session_state.db = firestore.client()
            
            # تحديد مسار التخزين العام (Public path)
            st.session_state.collection_path = f"artifacts/{APP_ID}/public/data/financial_reports"
            
        else:
            st.warning(fix_arabic("⚠️ لم يتم العثور على إعدادات Firebase. سيتم استخدام التخزين المؤقت للجلسة."))
            st.session_state.collection_path = None
    except Exception as e:
        # إذا فشلت التهيئة، نعود للتخزين المؤقت
        st.error(fix_arabic(f"❌ فشل في تهيئة Firebase: {e}"))
        st.session_state.collection_path = None
        
# ----------------------------------------------------------------
# 2. وظيفة الاستخلاص عبر Gemini (Multimodal)
# ----------------------------------------------------------------

def get_llm_multimodal_output(uploaded_file, client):
    """
    يرسل ملف PDF كبيانات مضمنة مباشرة لـ Gemini لاستخلاص الـ 20 حقلاً المحددة بتنسيق JSON.
    """
    if client is None:
        st.error(fix_arabic("🚨 لا يمكن التواصل مع Gemini. يرجى التحقق من توفير مفتاح API."))
        return None

    st.info(fix_arabic("⏳ جاري قراءة الملف وإرساله مباشرة لـ Gemini لبدء الاستخلاص..."))

    try:
        uploaded_file.seek(0)
        file_bytes = uploaded_file.read()
        mime_type = uploaded_file.type 

        if not mime_type or not mime_type.startswith(('application/pdf', 'image/')):
            st.error(fix_arabic(f"صيغة الملف ({mime_type}) غير مدعومة للاستخلاص البصري. الرجاء تحميل PDF أو صورة."))
            return None

        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        st.success(fix_arabic(f"✅ تم تجهيز الملف بنجاح ({uploaded_file.name})"))

        system_prompt = (
            "أنت محرك تحليل واستخلاص بيانات متميز ومتخصص في معالجة نصوص OCR العربية "
            "المشوشة والمقلوبة. مهمتك هي قراءة الملف المرفق وتحليل محتواه البصري والنصي بدقة. "
            "قم بتصحيح أي انعكاس (Bidi reversal) أو تشويش في النص العربي. يجب أن تكون المخرجات JSON فقط."
        )

        prompt_text = f"""
        بالتطبيق الصارم لتعليمات النظام، قم بتحليل الملف المرفق.
        
        **توجيهات البحث العامة (الـ 20 حقلاً المطلوب استخلاصها):**
        1.  **اسم المشتبه به:** استخرج **الاسم الكامل** (ثلاثي أو رباعي) للمشتبه به كما يظهر بجوار عبارة 'الوافد /' أو 'اسم العميل'.
        2.  **رقم الهوية:** استخرج رقم الهوية/الإقامة الوافد المكون من 10 أرقام.
        3.  **الجنسية:** استخرج الجنسية كما تظهر في حقل 'الجنسية'.
        4.  **تاريخ الميلاد الوافد:** استخرج تاريخ ميلاد الوافد/المشتبه به.
        5.  **تاريخ الدخول:** استخرج تاريخ دخول الوافد للمملكة.
        6.  **الحالة الاجتماعية:** استخرج الحالة الاجتماعية للوافد.
        7.  **المهنة:** استخرج المهنة كما تظهر في المستند.
        8.  **رقم الجوال:** استخرج رقم الجوال/الهاتف إن وُجد.
        9.  **المدينة:** استخرج مدينة إقامة العميل أو المدينة الأوضح.
        10. **رصيد الحساب:** استخرج الرصيد النهائي للحساب.
        11. **الدخل السنوي:** استخرج قيمة "إجمالي العمليات المضافة لحساب..." كتقدير للدخل السنوي.
        12. **رقم الصادر:** استخرج الرقم المكون من ٦ أرقام الذي يظهر بعد كلمة 'رقم الصادر' في أعلى المستند.
        13. **تاريخ الصادر:** استخرج التاريخ الهجري الذي يظهر بجوار حقل "التاريخ" المصاحب لـ "رقم الصادر".
        14. **رقم الوارد:** استخرج رقم الخطاب أو **رقم الوارد** الذي يظهر في ختم وزارة التجارة.
        15. **تاريخ الوارد:** استخرج تاريخ وصول الخطاب (التاريخ المصاحب لـ "رقم الوارد").
        16. **رقم صاحب العمل/ السجل التجاري:** استخرج رقم السجل التجاري للمنشأة أو رقم صاحب العمل.
        17. **سبب الاشتباه:** استخرج **الفقرة النصية الوصفية الكاملة والمفصلة** التي تصف سبب الاشتباه.
        18. **تاريخ الدارسة من:** استخرج تاريخ بداية فترة الدراسة.
        19. **تاريخ الدراسة الى:** استخرج تاريخ نهاية فترة الدراسة.
        20. **إجمالي الإيداع على الحساب اثناء الدراسة:** استخرج قيمة "إجمالي العمليات المضافة" أو "إجمالي الإيداع على الحساب اثناء الدراسة".
        
        **ملاحظة:** إذا لم تجد قيمة صريحة لأي حقل، ضع القيمة: 'غير متوفر'.
        
        الرجاء تقديم الإجابة بتنسيق JSON نقي (دون أي نص إضافي):
        {{
            "رقم الصادر": "القيمة المستخلصة.",
            "تاريخ الصادر": "القيمة المستخلصة.",
            "اسم المشتبه به": "القيمة المستخلصة كاملة.",
            "رقم الهوية": "القيمة المستخلصة.",
            "الجنسية": "القيمة المستخلصة.",
            "تاريخ الميلاد الوافد": "القيمة المستخلصة.",
            "تاريخ الدخول": "القيمة المستخلصة.",
            "الحالة الاجتماعية": "القيمة المستخلصة.",
            "المهنة": "القيمة المستخلصة.",
            "رقم الجوال": "القيمة المستخلصة.",
            "المدينة": "القيمة المستخلصة.",
            "رصيد الحساب": "القيمة المستخلصة بالريال.",
            "الدخل السنوي": "القيمة المستخلصة بالريال.",
            "رقم الوارد": "القيمة المستخلصة.",
            "تاريخ الوارد": "القيمة المستخلصة.",
            "رقم صاحب العمل/ السجل التجاري": "القيمة المستخلصة.",
            "سبب الاشتباه": "الفقرة النصية الوصفية الكاملة.",
            "تاريخ الدارسة من": "القيمة المستخلصة.",
            "تاريخ الدراسة الى": "القيمة المستخلصة.",
            "إجمالي الإيداع على الحساب اثناء الدراسة": "القيمة المستخلصة بالريال."
        }}
        """

        response_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            system_instruction=system_prompt,
            temperature=0.3
        )
        
        # 5. إرسال الطلب (ملف كـ Part + نص المطالبة)
        response = client.models.generate_content(
            model='gemini-2.5-pro',
            contents=[file_part, prompt_text],
            config=response_config
        )

        response_text = response.text.replace('\n', '').strip()
        
        # 6. تحليل الاستجابة
        if response_text.startswith('{') and response_text.endswith('}'):
             extracted_data = json.loads(response_text)
             return extracted_data
        else:
            st.error(fix_arabic(f"فشل في استخلاص بيانات JSON. تم الحصول على نص غير متوقع: {response_text[:100]}..."))
            return None

    except APIError as e:
        st.error(fix_arabic(f"🚨 خطأ في الاتصال بـ Gemini API: {e}"))
        return None
    except json.JSONDecodeError:
        st.error(fix_arabic("🚨 خطأ في تحليل بيانات JSON المستخلصة. الرجاء المحاولة مرة أخرى."))
        return None
    except Exception as e:
        st.error(fix_arabic(f"🚨 خطأ غير متوقع أثناء الاستخلاص: {e}"))
        return None


# -----------------------------------------------------
# 3. وظائف معالجة البيانات والتخزين (Firebase)
# -----------------------------------------------------

@st.cache_data(show_spinner=False)
def get_all_reports_from_firestore(db_client, collection_path):
    """تحميل جميع المستندات من Firestore."""
    if not db_client or not collection_path:
        return None
    
    try:
        reports_ref = db_client.collection(collection_path).stream()
        all_reports = []
        for report in reports_ref:
            report_data = report.to_dict()
            all_reports.append(report_data)
            
        # فرز البيانات حسب الرقم التسلسلي لضمان الترتيب في الإكسل
        all_reports.sort(key=lambda x: x.get('#', float('inf')))
        
        return all_reports

    except Exception as e:
        st.error(fix_arabic(f"❌ فشل في تحميل البيانات من Firestore: {e}"))
        return None


def add_report_to_firestore(db_client, collection_path, report_data):
    """إضافة بلاغ جديد إلى Firestore."""
    if not db_client or not collection_path:
        return False
    
    try:
        # يضيف مستند جديد بمعرف فريد (Auto-ID)
        db_client.collection(collection_path).add(report_data)
        st.cache_data.clear() # إجبار Streamlit على إعادة تحميل البيانات
        return True
    except Exception as e:
        st.error(fix_arabic(f"❌ فشل في حفظ البيانات في Firestore: {e}"))
        return False
        
        
def create_final_report(all_reports_data):
    """
    يحول قائمة القواميس (جميع التقارير) إلى DataFrame، يضبط ترتيب الأعمدة، وينشئ ملف Excel (xlsx).
    """
    if not all_reports_data:
        return None
        
    # نفس ترتيب الأعمدة المطلوبة بالضبط
    column_order = [
        "#", "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
        "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
        "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
        "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
        "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
        "إجمالي الإيداع على الحساب اثناء الدراسة"
    ]
    
    # تحويل القائمة الكاملة إلى DataFrame
    df = pd.DataFrame(all_reports_data)
    
    # ضمان وجود جميع الأعمدة المطلوبة في DataFrame بالترتيب الصحيح
    final_cols = []
    for col in column_order:
        if col in df.columns:
            final_cols.append(col)
        else:
            df[col] = ''
            final_cols.append(col)
            
    # تطبيق الترتيب النهائي
    df = df[final_cols]
    
    # تطبيق دالة fix_arabic على جميع القيم النصية قبل التصدير
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(lambda x: get_display(reshape(str(x))) if pd.notna(x) else x)
            
    # إنشاء مخرج Excel في الذاكرة
    output = io.BytesIO()
    
    try:
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        sheet_name = fix_arabic('بيانات البلاغات')
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        # تهيئة التنسيق لملف Excel
        workbook  = writer.book
        worksheet = writer.sheets[sheet_name]
        worksheet.right_to_left()

        # تنسيق العمود 17 (سبب الاشتباه) ليكون ملتفاً وواسعاً
        col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
        worksheet.set_column(17, 17, 60, col_format) 
        
        writer.close()
        output.seek(0)
        
        return output.read()
        
    except Exception as e:
        st.error(fix_arabic(f"🚨 حدث خطأ أثناء إنشاء ملف Excel: {e}"))
        return None

# ----------------------------------------------------------------
# 4. واجهة التطبيق الرئيسية (Streamlit)
# ----------------------------------------------------------------

def main():
    st.set_page_config(page_title=fix_arabic("أتمتة استخلاص التقارير المالية"), layout="wide")
    st.markdown(f"<h1 style='text-align: right;'>{fix_arabic('استخلاص التقارير المالية الآلي 🤖 (سجل بيانات موحد)')}</h1>", unsafe_allow_html=True)
    st.markdown("---")
    
    # 1. محاولة جلب جميع البيانات المخزنة من Firebase Firestore
    all_reports_data = get_all_reports_from_firestore(
        st.session_state.get('db'), 
        st.session_state.get('collection_path')
    )
    
    # 2. تحديد عدد البلاغات الحالية واختيار وضع التخزين
    if st.session_state.get('collection_path') and all_reports_data is not None:
        reports_count = len(all_reports_data)
        st.info(fix_arabic(f"💾 وضع التخزين: دائم (Firebase Firestore). عدد البلاغات المخزنة: {reports_count} بلاغ."))
    else:
        # استخدام التخزين المؤقت في حال فشل الاتصال بقاعدة البيانات
        if 'report_data_temp' not in st.session_state:
            st.session_state.report_data_temp = []
        all_reports_data = st.session_state.report_data_temp
        reports_count = len(all_reports_data)
        st.warning(fix_arabic(f"⚠️ وضع التخزين: مؤقت (جلسة Streamlit). عدد البلاغات المخزنة: {reports_count} بلاغ. **ملاحظة: ستفقد البيانات عند إغلاق المتصفح.**"))


    uploaded_file = st.file_uploader(
        fix_arabic("📂 قم بتحميل ملف التقرير المالي (PDF/Excel) هنا:"),
        type=["pdf", "xlsx", "xls", "csv"],
        accept_multiple_files=False
    )

    if uploaded_file is not None:
        st.success(fix_arabic(f"تم تحميل ملف: {uploaded_file.name}"))
        
        if st.button(fix_arabic("🚀 بدء الاستخلاص والإضافة للسجل الموحد"), key="start_extraction"):
            
            # التأكد من وجود مفتاح Gemini
            if not GEMINI_API_KEY:
                st.error(fix_arabic("🚨 يرجى لصق مفتاح Gemini API في الكود قبل بدء الاستخلاص."))
                return

            with st.spinner(fix_arabic('⏳ جاري تحليل واستخلاص البيانات وتجهيز البلاغ... (قد يستغرق 30-60 ثانية)')):
                
                extracted_data = get_llm_multimodal_output(uploaded_file, client)
                
                if extracted_data:
                    
                    # 3. تحديد الرقم التسلسلي الجديد
                    next_index = reports_count + 1
                    extracted_data["#"] = next_index 
                    
                    # 4. حفظ البيانات (في Firestore أو مؤقتاً)
                    is_saved = False
                    
                    # إعادة تحميل البيانات من Firestore للتأكد من أحدث نسخة (لضمان الرقم التسلسلي الصحيح)
                    current_reports_data = get_all_reports_from_firestore(st.session_state.get('db'), st.session_state.get('collection_path'))
                    if current_reports_data is not None:
                        # تحديث الرقم التسلسلي بناءً على البيانات الأحدث
                        extracted_data["#"] = len(current_reports_data) + 1
                        all_reports_data = current_reports_data
                        reports_count = len(current_reports_data)

                    if st.session_state.get('collection_path') and st.session_state.get('db'):
                        # حفظ دائم
                        is_saved = add_report_to_firestore(st.session_state.db, st.session_state.collection_path, extracted_data)
                        if is_saved:
                            # إعادة تحميل البيانات من Firestore بعد الإضافة لضمان التحديث الفوري
                            all_reports_data = get_all_reports_from_firestore(st.session_state.db, st.session_state.collection_path)
                    else:
                        # حفظ مؤقت
                        st.session_state.report_data_temp.append(extracted_data)
                        is_saved = True
                        all_reports_data = st.session_state.report_data_temp


                    if is_saved and all_reports_data:
                        
                        # 5. عرض البيانات المستخلصة للبلاغ الأخير
                        st.markdown(f"<h3 style='text-align: right;'>{fix_arabic(f'✅ البيانات المستخلصة للبلاغ رقم {extracted_data['#']} (تحقق سريع)')}</h3>", unsafe_allow_html=True)
                        st.markdown("---")
                        
                        last_report = extracted_data # نستخدم البيانات المستخلصة الجديدة مباشرة للعرض
                        
                        for key, value in last_report.items():
                            display_key = fix_arabic(key)
                            display_value = fix_arabic(value)
                            
                            # الحل النهائي لـ Bidi: عرض المفتاح والقيمة مفصولين بوضوح داخل وسم RTL
                            html_line = f"""
                            <div style="direction: rtl; text-align: right; margin-bottom: 5px; line-height: 1.5; font-size: 16px;">
                                <span style="font-weight: bold; color: #155e75;">{display_key}:</span>
                                <span style="margin-right: 5px;">{display_value}</span>
                            </div>
                            """
                            st.markdown(html_line, unsafe_allow_html=True)

                        st.markdown("---")
                        
                        # 6. إنشاء ملف الإكسل الموحد من جميع البيانات المخزنة
                        excel_data_bytes = create_final_report(all_reports_data)
                        
                        if excel_data_bytes:
                            st.subheader(fix_arabic("🎉 تم حفظ البلاغ! قم بتحميل السجل الموحد"))
                            st.balloons()
                            
                            st.download_button(
                                label=fix_arabic("⬇️ تحميل سجل بيانات البلاغ الموحد (بيانات البلاغ.xlsx)"),
                                data=excel_data_bytes,
                                file_name=fix_arabic("بيانات البلاغ.xlsx"),
                                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                            )
                        else:
                            st.error(fix_arabic("❌ فشل في إنشاء ملف Excel. الرجاء مراجعة سجل الأخطاء."))
                    else:
                        st.error(fix_arabic("❌ فشلت عملية حفظ البيانات. الرجاء المحاولة مرة أخرى."))


if __name__ == '__main__':
    main()
