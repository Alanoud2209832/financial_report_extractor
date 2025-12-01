import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from google import genai
from arabic_reshaper import reshape
from bidi.algorithm import get_display
from db import connect_db

conn = connect_db()

if conn:
    cur = conn.cursor()
    cur.execute("SELECT NOW();")   # اختبار الاتصال فقط
    print("Database Time:", cur.fetchone())

    cur.close()
    conn.close()

# ----------------------------------------------------------------------
# 1. إعداد مفتاح Gemini API (مطلوب: استخدمي المفتاح الجديد)
# ----------------------------------------------------------------------
# 🚨 ملاحظة هامة: يجب لصق مفتاح Gemini API الجديد والصالح هنا.
GEMINI_API_KEY = "AIzaSyCeNFMTQjPhKMk0hN5qA_Lk-256RpExmN0" # ⬅️ الصقي المفتاح الجديد هنا

# ----------------------------------------------------------------------
# 2. إعداد الاتصال بقاعدة بيانات Firestore (الطريقة الآمنة)
# ----------------------------------------------------------------------

# 🚨 تحذير أمني: تم إزالة مفتاح الخدمة الخاص بك من الكود (الكائن FIRESTORE_CREDENTIALS) 
# يجب الآن الاعتماد على ملف .streamlit/secrets.toml أو متغيرات البيئة لضمان الأمان.

# هذه الدالة تحاول جلب بيانات الاعتماد بأمان
def get_firestore_credentials():
    try:
        # القراءة من st.secrets (سواء من ملف secrets.toml محلياً أو متغيرات بيئة Streamlit Cloud)
        secret_dict = st.secrets["firestore"]

        # معالجة المفتاح الخاص المتعدد الأسطر (Private Key)
        # إذا تم تمريره كنص واحد مع ترميز \n (كما يحدث غالباً عند استخدام secrets.toml/متغيرات البيئة)
        if isinstance(secret_dict, dict) and "private_key" in secret_dict:
            secret_dict["private_key"] = secret_dict["private_key"].replace('\\n', '\n')

        return secret_dict
        
    except KeyError:
        # إذا لم يتم العثور على المفتاح، نرسل خطأ واضح للمستخدم
        st.error("❌ خطأ: لم يتم العثور على أسرار Firestore في st.secrets. "
                 "يرجى التأكد من إنشاء ملف `.streamlit/secrets.toml` يحتوي على المفتاح.")
        return None
    except Exception as e:
        st.error(f"❌ خطأ غير متوقع أثناء قراءة أسرار Firestore: {e}")
        return None

# محاولة الحصول على بيانات الاعتماد
firestore_creds = get_firestore_credentials()
db_client = None

if firestore_creds:
    # التحقق من التهيئة وتجنب الخطأ إذا تم التهيئة مسبقاً
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(firestore_creds) 
            firebase_admin.initialize_app(cred)
            db_client = firestore.client()
            st.session_state['db'] = db_client
            st.success("🎉 تم الاتصال بـ Firestore بنجاح!")
        except Exception as e:
            st.error(f"❌ خطأ في الاتصال بـ Firestore. يرجى مراجعة مفتاح الخدمة في `secrets.toml`. الخطأ: {e}")
            st.session_state['db'] = None
    else:
        db_client = firestore.client()
        st.session_state['db'] = db_client
else:
    st.session_state['db'] = None


# ----------------------------------------------------------------------
# 3. الدوال المساعدة للغة العربية
# ----------------------------------------------------------------------
def fix_arabic(text):
    """يصلح عرض النصوص العربية في Streamlit."""
    return get_display(reshape(str(text)))

# ----------------------------------------------------------------------
# 4. واجهة التطبيق الرئيسية
# ----------------------------------------------------------------------
st.set_page_config(layout="wide", page_title=fix_arabic("محلل التقارير المالية الذكي"))

st.title(fix_arabic("محلل التقارير المالية المدعوم بالذكاء الاصطناعي"))

# ----------------------------------------------------------------------
# 5. منطقة تحميل الملفات
# ----------------------------------------------------------------------
uploaded_file = st.file_uploader(fix_arabic("تحميل التقرير المالي (PDF أو CSV)"), type=["pdf", "csv"])

if uploaded_file is not None:
    st.success(fix_arabic(f"تم تحميل الملف بنجاح: {uploaded_file.name}"))
    
    # ----------------------------------------------------------------------
    # 6. معالجة الملفات وتحليلها (يجب استكمال هذه المنطقة بناءً على متطلبات التحليل)
    # ----------------------------------------------------------------------
    if 'analysis_done' not in st.session_state:
        st.session_state['analysis_done'] = False
        st.session_state['report_data'] = None

    if st.button(fix_arabic("بدء التحليل")):
        # هذه خطوة تحليلية افتراضية. ستحتاج إلى دمج Gemini Vision هنا لتحليل PDF
        # أو تحليل بيانات CSV/Excel
        
        # لنفترض أن التحليل يخرج بملخص:
        summary = {
            "اسم_التقرير": uploaded_file.name,
            "تاريخ_التحليل": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ملخص_النتائج": fix_arabic("ملخص مفصل للتقرير المالي مع تحديد مؤشرات الخطر."),
            "مؤشرات_الخطر": fix_arabic("عدم تناسب حجم العمليات مع الدخل المعلن."),
        }
        
        st.session_state['report_data'] = summary
        st.session_state['analysis_done'] = True
        st.session_state['file_name'] = uploaded_file.name

    if st.session_state['analysis_done']:
        st.subheader(fix_arabic("نتائج التحليل"))
        data = st.session_state['report_data']
        
        # عرض النتائج
        st.json(data)

        # ----------------------------------------------------------------------
        # 7. وظيفة حفظ البيانات في Firestore
        # ----------------------------------------------------------------------
        if st.session_state['db'] is not None and st.button(fix_arabic("حفظ التقرير في قاعدة البيانات")):
            try:
                db = st.session_state['db']
                # نحدد المسار الذي ستُحفظ فيه البيانات في Firestore
                # المسار المتبع: artifacts/{project_id}/reports/{file_name}
                reports_collection = db.collection("artifacts").document("project-6a5a2").collection("reports")
                
                # إضافة البيانات كـ مستند جديد
                reports_collection.add(data)
                
                st.success(fix_arabic("تم حفظ التقرير بنجاح في قاعدة البيانات!"))
                
                # تحديث حالة التحقق اليدوي
                st.info(fix_arabic("يرجى الآن زيارة Firebase Console للتأكد من ظهور مجموعة 'artifacts' ومجموعة 'reports' داخلها."))

            except Exception as e:
                st.error(fix_arabic(f"فشل حفظ البيانات: {e}"))
                st.warning(fix_arabic("فشل الحفظ. قد تكون المشكلة في أذونات الكتابة في Firestore."))


# ----------------------------------------------------------------------
# 8. شاشة البدء (عند عدم وجود ملف مُحمّل)
# ----------------------------------------------------------------------
if uploaded_file is None:
    st.info(fix_arabic("يرجى تحميل تقرير مالي لبدء التحليل. يدعم الملفات بصيغة PDF و CSV."))

# ----------------------------------------------------------------------
# 9. تذكير حالة المفتاح
# ----------------------------------------------------------------------
if GEMINI_API_KEY == "AIzaSy...":
    st.warning(fix_arabic("تذكير: يجب استبدال 'AIzaSy...' بمفتاح Gemini API الصالح الجديد لبدء التحليل الفعلي."))
