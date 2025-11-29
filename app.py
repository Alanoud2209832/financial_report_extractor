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
from firebase_admin import initialize_app, firestore, credentials, get_app
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

# دالة تصحيح النص العربي (تستخدم Reshaper و BiDi)
def fix_arabic(text):
    """يعالج النصوص العربية لضمان العرض الصحيح (من اليمين لليسار)."""
    if isinstance(text, str) and text:
        reshaped_text = reshape(text)
        return get_display(reshaped_text)
    return text
    
# دالة مساعدة لتغليف النص (لتصحيح مشكلة Bidi في Streamlit UI)
def rtl_markdown(content, style_type="info"):
    """
    يعرض المحتوى داخل وسم HTML مع فرض الاتجاه اليمين لليسار (RTL).
    """
    
    # تحديد تنسيق Streamlit (باستخدام CSS مضمن)
    styles = {
        "info": {"bg": "#eff6ff", "border": "#93c5fd", "text": "#1d4ed8"},
        "warning": {"bg": "#fffbeb", "border": "#fcd34d", "text": "#b45309"},
        "success": {"bg": "#ecfdf5", "border": "#6ee7b7", "text": "#059669"},
        "error": {"bg": "#fef2f2", "border": "#fca5a5", "text": "#dc2626"},
    }
    
    style = styles.get(style_type, styles["info"])
    
    html_template = f"""
    <div style="direction: rtl; text-align: right; 
                background-color: {style['bg']}; 
                border-left: 5px solid {style['border']}; 
                padding: 10px; border-radius: 4px; color: {style['text']}; 
                font-size: 16px; margin-bottom: 10px;">
        {content}
    </div>
    """
    st.markdown(html_template, unsafe_allow_html=True)


# -----------------------------------------------------
# 🚀 1.1 تهيئة Firebase Firestore للتخزين الدائم
# -----------------------------------------------------

# تهيئة Firebase باستخدام متغيرات البيئة في Canvas
if 'db' not in st.session_state:
    try:
        # قراءة متغيرات البيئة (المتاحة في Canvas)
        FIREBASE_CONFIG_JSON = os.environ.get('__firebase_config', '{}')
        FIREBASE_CONFIG = json.loads(FIREBASE_CONFIG_JSON)
        APP_ID = os.environ.get('__app_id', 'default-app-id')
        
        # التأكد من وجود البيانات الأساسية للتهيئة
        if FIREBASE_CONFIG and APP_ID and FIREBASE_CONFIG_JSON != '{}':
            
            app_initialized = False
            try:
                get_app() 
                app_initialized = True
            except ValueError:
                pass
                
            if not app_initialized:
                 cred = credentials.Certificate(FIREBASE_CONFIG)
                 initialize_app(cred)
                 
            st.session_state.db = firestore.client()
            
            st.session_state.collection_path = f"artifacts/{APP_ID}/public/data/financial_reports"
            
        else:
            rtl_markdown(fix_arabic("⚠️ لم يتم العثور على إعدادات Firebase (Config). سيتم استخدام التخزين المؤقت للجلسة."), "warning")
            st.session_state.collection_path = None
    except Exception as e:
        rtl_markdown(fix_arabic(f"❌ فشل في تهيئة Firebase بسبب خطأ غير متوقع: {e}"), "error")
        st.session_state.collection_path = None
        
# ----------------------------------------------------------------
# 2. وظيفة الاستخلاص عبر Gemini (Multimodal)
# ----------------------------------------------------------------

def get_llm_multimodal_output(uploaded_file, client):
    """
    يرسل ملف PDF كبيانات مضمنة مباشرة لـ Gemini لاستخلاص الـ 20 حقلاً المحددة بتنسيق JSON.
    """
    if client is None:
        rtl_markdown(fix_arabic("🚨 لا يمكن التواصل مع Gemini. يرجى التحقق من توفير مفتاح API."), "error")
        return None

    rtl_markdown(fix_arabic("⏳ جاري قراءة الملف وإرساله مباشرة لـ Gemini لبدء الاستخلاص..."), "info")

    try:
        uploaded_file.seek(0)
        file_bytes = uploaded_file.read()
        mime_type = uploaded_file.type 

        if not mime_type or not mime_type.startswith(('application/pdf', 'image/')):
            rtl_markdown(fix_arabic(f"صيغة الملف ({mime_type}) غير مدعومة للاستخلاص البصري. الرجاء تحميل PDF أو صورة."), "error")
            return None

        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        rtl_markdown(fix_arabic(f"✅ تم تجهيز الملف بنجاح ({uploaded_file.name})"), "success")

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
            rtl_markdown(fix_arabic(f"فشل في استخلاص بيانات JSON. تم الحصول على نص غير متوقع: {response_text[:100]}..."), "error")
            return None

    except APIError as e:
        rtl_markdown(fix_arabic(f"🚨 خطأ في الاتصال بـ Gemini API: {e}"), "error")
        return None
    except json.JSONDecodeError:
        rtl_markdown(fix_arabic("🚨 خطأ في تحليل بيانات JSON المستخلصة. الرجاء المحاولة مرة أخرى."), "error")
        return None
    except Exception as e:
        rtl_markdown(fix_arabic(f"🚨 خطأ غير متوقع أثناء الاستخلاص: {e}"), "error")
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
            report_data['doc_id'] = report.id 
            all_reports.append(report_data)
            
        all_reports.sort(key=lambda x: x.get('#', float('inf')))
        
        return all_reports

    except Exception as e:
        if "No project has been set" in str(e) or "A default Firebase App has not been initialized" in str(e):
             # استخدام st.warning/st.error هنا لأنها خارج دالة rtl_markdown
             st.warning(fix_arabic("⚠️ لم يتم تهيئة Firebase بنجاح. قد يكون هناك مشكلة في إعدادات البيئة التلقائية."))
             return []
        else:
            st.error(fix_arabic(f"❌ فشل في تحميل البيانات من Firestore: {e}"))
            return None


def add_report_to_firestore(db_client, collection_path, report_data):
    """إضافة بلاغ جديد إلى Firestore."""
    if not db_client or not collection_path:
        rtl_markdown(fix_arabic("❌ فشل في الحفظ: لم يتم تهيئة قاعدة البيانات."), "error")
        return False
    
    data_to_save = report_data.copy()
    if 'doc_id' in data_to_save:
        del data_to_save['doc_id']
        
    try:
        db_client.collection(collection_path).add(data_to_save)
        st.cache_data.clear()
        return True
    except Exception as e:
        rtl_markdown(fix_arabic(f"❌ فشل في حفظ البيانات في Firestore: {e}"), "error")
        return False
        
        
def create_final_report(all_reports_data):
    """
    يحول قائمة القواميس (جميع التقارير) إلى DataFrame، يضبط ترتيب الأعمدة، وينشئ ملف Excel (xlsx).
    """
    if not all_reports_data:
        return None
        
    column_order = [
        "#", "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
        "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
        "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
        "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
        "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
        "إجمالي الإيداع على الحساب اثناء الدراسة"
    ]
    
    df = pd.DataFrame(all_reports_data)
    
    final_cols = []
    for col in column_order:
        if col in df.columns:
            final_cols.append(col)
        else:
            df[col] = ''
            final_cols.append(col)
            
    final_cols_filtered = [col for col in final_cols if col in df.columns and col != 'doc_id']
    df = df[final_cols_filtered]
    
    # 🚨 هذه الخطوة حاسمة: تطبيق تصحيح BiDi على جميع بيانات DataFrame قبل التصدير إلى Excel
    for col in df.columns:
        if df[col].dtype == 'object':
            # استخدام get_display(reshape()) هنا ضروري لملف Excel لضمان عدم عكس النص
            df[col] = df[col].apply(lambda x: get_display(reshape(str(x))) if pd.notna(x) else x)
            
    output = io.BytesIO()
    
    try:
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        # 🚨 تصحيح اسم الورقة باستخدام fix_arabic
        sheet_name = fix_arabic('بيانات البلاغات')
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        workbook  = writer.book
        worksheet = writer.sheets[sheet_name]
        worksheet.right_to_left()

        if len(final_cols_filtered) > 17:
            col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
            worksheet.set_column(17, 17, 60, col_format) 
        
        writer.close()
        output.seek(0)
        
        return output.read()
        
    except Exception as e:
        # استخدام st.error هنا لأنها خارج دالة rtl_markdown
        st.error(fix_arabic(f"🚨 حدث خطأ أثناء إنشاء ملف Excel: {e}"))
        return None

# ----------------------------------------------------------------
# 4. واجهة التطبيق الرئيسية (Streamlit)
# ----------------------------------------------------------------

def main():
    st.set_page_config(page_title=fix_arabic("أتمتة استخلاص التقارير المالية"), layout="wide")
    # 🚨 فرض الاتجاه على العنوان الرئيسي
    st.markdown(f"<h1 style='text-align: right; direction: rtl;'>{fix_arabic('استخلاص التقارير المالية الآلي 🤖 (سجل بيانات موحد)')}</h1>", unsafe_allow_html=True)
    st.markdown("---")
    
    all_reports_data = get_all_reports_from_firestore(
        st.session_state.get('db'), 
        st.session_state.get('collection_path')
    )
    
    if st.session_state.get('collection_path') and all_reports_data is not None:
        reports_count = len(all_reports_data)
        # 🚨 استخدام دالة rtl_markdown للتحذيرات والمعلومات
        rtl_markdown(fix_arabic(f"💾 وضع التخزين: دائم (Firebase Firestore). عدد البلاغات المخزنة: {reports_count} بلاغ."), "info")
    else:
        if 'report_data_temp' not in st.session_state:
            st.session_state.report_data_temp = []
        all_reports_data = st.session_state.report_data_temp
        reports_count = len(all_reports_data)
        # 🚨 استخدام دالة rtl_markdown للتحذيرات والمعلومات
        rtl_markdown(fix_arabic(f"⚠️ وضع التخزين: مؤقت (جلسة Streamlit). عدد البلاغات المخزنة: {reports_count} بلاغ. **ملاحظة: ستفقد البيانات عند إغلاق المتصفح.**"), "warning")


    # 🚨 استخدام fix_arabic لجميع عناصر UI
    uploaded_file = st.file_uploader(
        fix_arabic("📂 قم بتحميل ملف التقرير المالي (PDF/Excel) هنا:"),
        type=["pdf", "xlsx", "xls", "csv"],
        accept_multiple_files=False
    )

    if uploaded_file is not None:
        rtl_markdown(fix_arabic(f"تم تحميل ملف: {uploaded_file.name}"), "success")
        
        # 🚨 استخدام fix_arabic لزر بدء الاستخلاص
        if st.button(fix_arabic("🚀 بدء الاستخلاص والإضافة للسجل الموحد"), key="start_extraction"):
            
            if not GEMINI_API_KEY:
                rtl_markdown(fix_arabic("🚨 يرجى لصق مفتاح Gemini API في الكود قبل بدء الاستخلاص."), "error")
                return

            with st.spinner(fix_arabic('⏳ جاري تحليل واستخلاص البيانات وتجهيز البلاغ... (قد يستغرق 30-60 ثانية)')):
                
                extracted_data = get_llm_multimodal_output(uploaded_file, client)
                
                if extracted_data:
                    
                    # 3. تحديث البيانات الأخيرة للرقم التسلسلي
                    current_reports_data = get_all_reports_from_firestore(st.session_state.get('db'), st.session_state.get('collection_path'))
                    if current_reports_data is not None:
                        extracted_data["#"] = len(current_reports_data) + 1
                        all_reports_data = current_reports_data

                    # 4. حفظ البيانات (في Firestore أو مؤقتاً)
                    is_saved = False
                    
                    if st.session_state.get('collection_path') and st.session_state.get('db'):
                        is_saved = add_report_to_firestore(st.session_state.db, st.session_state.collection_path, extracted_data)
                        if is_saved:
                            all_reports_data = get_all_reports_from_firestore(st.session_state.db, st.session_state.collection_path)
                    else:
                        st.session_state.report_data_temp.append(extracted_data)
                        is_saved = True
                        all_reports_data = st.session_state.report_data_temp


                    if is_saved and all_reports_data:
                        
                        # 5. عرض البيانات المستخلصة للبلاغ الأخير
                        # 🚨 فرض الاتجاه على العنوان
                        st.markdown(f"<h3 style='text-align: right; direction: rtl; color: #059669;'>{fix_arabic(f'✅ البيانات المستخلصة للبلاغ رقم {extracted_data['#']} (تحقق سريع)')}</h3>", unsafe_allow_html=True)
                        st.markdown("---")
                        
                        last_report = extracted_data
                        
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
                            # 🚨 فرض الاتجاه على العنوان
                            st.markdown(f"<h3 style='text-align: right; direction: rtl;'>{fix_arabic('🎉 تم حفظ البلاغ! قم بتحميل السجل الموحد')}</h3>", unsafe_allow_html=True)
                            st.balloons()
                            
                            # 🚨 استخدام fix_arabic لزر التحميل
                            st.download_button(
                                label=fix_arabic("⬇️ تحميل سجل بيانات البلاغ الموحد (بيانات البلاغ.xlsx)"),
                                data=excel_data_bytes,
                                file_name=fix_arabic("بيانات البلاغ.xlsx"),
                                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                            )
                        else:
                            rtl_markdown(fix_arabic("❌ فشل في إنشاء ملف Excel. الرجاء مراجعة سجل الأخطاء."), "error")
                    else:
                        rtl_markdown(fix_arabic("❌ فشلت عملية حفظ البيانات. الرجاء المحاولة مرة أخرى."), "error")


if __name__ == '__main__':
    main()
