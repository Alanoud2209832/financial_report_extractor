import streamlit as st
import pandas as pd
import json
import io
import time
import sqlite3
import fitz # استيراد مكتبة PyMuPDF للتعامل مع PDF
from PIL import Image # مكتبة Pillow لمعالجة الصور
from google import genai
from google.genai.errors import APIError

# ----------------------------------------------------------------
# 1. إعدادات API والنصوص العربية وتهيئة قاعدة البيانات
# ----------------------------------------------------------------

# 🚨 هام: يجب تعيين مفتاح API الخاص بكِ هنا!
# يرجى استبدال النص الفارغ التالي بمفتاح Gemini API الصالح
# (المفتاح الذي قمتِ بلصقه سابقاً هو مفتاح مثال غير صالح وسيسبب خطأ API.)
GEMINI_API_KEY = "AIzaSyBVJvH_Z5AX9dwXR7UFhbeo9iB5-aL-rZI" # ⬅️ يرجى لصق المفتاح الصالح هنا بين علامات التنصيص

# تهيئة موديل Gemini
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    "أنت خبير في تحليل التقارير المالية. مهمتك هي قراءة النص والصورة المستخرجة من وثيقة "
    "مالية وتحويله إلى كائن JSON وفقًا للمخطط المحدد. يجب أن تكون دقيقًا جدًا في "
    "استخلاص القيم وأن تتأكد من مطابقتها لأسماء الحقول المطلوبة باللغة الإنجليزية."
)

# أسماء الحقول المطلوبة (باللغة الإنجليزية للمطابقة مع JSON Schema)
REPORT_FIELDS = [
    "issue_number", "issue_date", "suspect_name", "id_number", "nationality", 
    "birth_date", "entry_date", "social_status", "profession", "phone_number", 
    "city", "account_balance", "annual_income", "incoming_number", "incoming_date", 
    "employer_id", "suspicion_reason", "study_start_date", "study_end_date", 
    "total_deposit_during_study"
]

# مخطط الاستجابة لـ Gemini (JSON Schema)
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {field: {"type": "STRING"} for field in REPORT_FIELDS},
    "propertyOrdering": REPORT_FIELDS
}

# ----------------------------------------------------------------
# 2. وظائف SQLite (التخزين الدائم)
# ----------------------------------------------------------------

DB_FILE = 'financial_data.db'

# إنشاء اتصال بقاعدة البيانات
@st.cache_resource
def get_db_connection():
    """ينشئ اتصال قاعدة بيانات SQLite ويكرر المحاولة عند الفشل."""
    conn = None
    max_retries = 5
    
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            conn.row_factory = sqlite3.Row # لجعل النتائج قابلة للوصول بالاسم
            return conn
        except sqlite3.Error as e:
            time.sleep(2 ** attempt) # انتظار أطول بعد كل فشل
            if attempt == max_retries - 1:
                st.error(f"فشل الاتصال بقاعدة البيانات SQLite بعد {max_retries} محاولات. الخطأ: {e}")
                return None
    return None

# تهيئة الجدول في قاعدة البيانات
def init_db(conn):
    """ينشئ جدول 'reports' إذا لم يكن موجودًا."""
    if conn:
        try:
            field_definitions = ", ".join([f"{field} TEXT" for field in REPORT_FIELDS])
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT,
                    {field_definitions},
                    extraction_timestamp TEXT
                );
            """)
            conn.commit()
        except sqlite3.Error as e:
            st.error(f"خطأ في تهيئة جدول قاعدة البيانات: {e}")

# جلب جميع البيانات من قاعدة البيانات
def fetch_all_reports(conn):
    """يجلب جميع السجلات من جدول 'reports'."""
    if conn:
        try:
            reports = conn.execute("SELECT * FROM reports ORDER BY extraction_timestamp DESC").fetchall()
            return [dict(report) for report in reports]
        except sqlite3.Error as e:
            st.error(f"خطأ في جلب التقارير: {e}")
            return []
    return []

# إدخال تقرير جديد إلى قاعدة البيانات
def insert_report(conn, data):
    """يدخل بيانات التقرير المستخلصة إلى قاعدة البيانات."""
    if conn:
        try:
            columns = ', '.join(data.keys())
            placeholders = ', '.join('?' * len(data))
            values = tuple(data.values())
            
            conn.execute(f"INSERT INTO reports ({columns}) VALUES ({placeholders})", values)
            conn.commit()
            return True
        except sqlite3.Error as e:
            st.error(f"خطأ في حفظ التقرير في قاعدة البيانات: {e}")
            return False
    return False

# ----------------------------------------------------------------
# 3. وظيفة معالجة الملفات والاستخلاص
# ----------------------------------------------------------------

def convert_pdf_to_images(file_bytes):
    """تحويل ملف PDF (كـ bytes) إلى قائمة من صور PNG كـ bytes."""
    
    # ⚠️ ملاحظة: نحن نرسل الصفحة الأولى فقط لتجنب الزيادة الكبيرة في حجم الطلب والتكلفة.
    try:
        pdf_document = fitz.open(stream=file_bytes, filetype="pdf")
        
        # استهداف الصفحة الأولى فقط
        page = pdf_document.load_page(0)
        
        # إنشاء مصفوفة بكسل عالية الدقة
        # زوم 3.0 لتحسين وضوح النص
        matrix = fitz.Matrix(3.0, 3.0)
        
        # إنشاء صورة PNG من الصفحة
        pix = page.get_pixmap(matrix=matrix)
        
        # تحويل بيانات البكسل إلى بايتات قابلة للإرسال
        img_bytes = pix.tobytes(output='png')
        
        return [img_bytes]
    except Exception as e:
        st.error(f"حدث خطأ أثناء تحويل PDF إلى صورة: {e}")
        return []

def extract_financial_data(file_bytes, file_name, file_type):
    """
    يتلقى بيانات الملف ويستخدم Gemini API لاستخلاص البيانات المالية
    وإدخالها مباشرة في قاعدة البيانات.
    """
    if not GEMINI_API_KEY:
        st.error("الرجاء تحديث 'GEMINI_API_KEY' في الكود بمفتاح صالح قبل تحميل الملف.")
        return False
        
    response = None # تهيئة المتغير
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # 1. تحديد المحتوى المتعدد الوسائط (Multimodal Content)
        content_parts = [
            "قم باستخلاص جميع البيانات من هذه الوثيقة المالية "
            "وحوّلها إلى كائن JSON يطابق المخطط المحدد بدقة. "
            "إذا لم تتمكن من العثور على قيمة حقل معين، استخدم 'N/A'."
        ]
        
        if file_type == 'pdf':
            st.info("تم الكشف عن ملف PDF. جاري تحويل الصفحة الأولى إلى صورة...")
            image_bytes_list = convert_pdf_to_images(file_bytes)
            
            if not image_bytes_list:
                return False # فشل التحويل
                
            # إضافة الصورة (الـ bytes) إلى محتويات الطلب
            for img_bytes in image_bytes_list:
                content_parts.append({
                    "inlineData": {
                        "data": img_bytes,
                        "mimeType": "image/png" # الآن أصبح نوع الملف صورة PNG
                    }
                })
        
        elif file_type in ['png', 'jpg', 'jpeg']:
            # إضافة الصورة الأصلية مباشرة
            content_parts.append({
                "inlineData": {
                    "data": file_bytes,
                    "mimeType": f"image/{file_type}" 
                }
            })
        else:
            st.error(f"نوع الملف غير مدعوم: {file_type}")
            return False

        # 2. إعدادات التوليد
        config = {
            "systemInstruction": SYSTEM_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            # إزالة Google Search مؤقتاً لتجنب إدخال تعقيد غير ضروري في هذا النوع من مهام استخلاص البيانات المحددة.
            # "tools": [{"google_search": {}}]
        }

        # 3. طلب توليد المحتوى
        st.info(f"جاري استخلاص البيانات من '{file_name}'...")
        
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=content_parts,
            config=config,
        )

        # 4. معالجة الاستجابة وحفظها في SQLite
        json_output = response.text
        extracted_data = json.loads(json_output)
        
        # إضافة البيانات الأساسية
        extracted_data['file_name'] = file_name
        extracted_data['extraction_timestamp'] = pd.Timestamp.now().isoformat()

        conn = get_db_connection()
        if conn and insert_report(conn, extracted_data):
            st.success(f"تم استخلاص وحفظ التقرير: '{file_name}' بنجاح!")
            return True
        else:
            st.error("فشل في حفظ البيانات في قاعدة البيانات.")
            return False

    except APIError as e:
        # عرض الخطأ الذي تلقيناه من الـ API بوضوح
        st.error(f"خطأ في الاتصال بـ Gemini API. تأكدي من صحة المفتاح. الخطأ: {e}")
    except json.JSONDecodeError:
        st.error("فشل في تفسير استجابة النموذج كـ JSON. الرجاء المحاولة مرة أخرى.")
    except Exception as e:
        # إذا حدث أي خطأ غير متوقع آخر، سيتم الإبلاغ عنه بوضوح
        st.error(f"حدث خطأ غير متوقع: {e}")
        # إذا كانت هناك استجابة من النموذج، اعرضها للمساعدة في التصحيح
        if response and response.text:
            st.code(response.text)
    return False

# ----------------------------------------------------------------
# 4. واجهة المستخدم (Streamlit UI)
# ----------------------------------------------------------------

st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")

st.markdown("""
<style>
    .reportview-container .main {
        padding-top: 2rem;
    }
    .stButton>button {
        background-color: #0F9D58; 
        color: white; 
        border-radius: 8px;
        padding: 10px 20px;
    }
    .stApp {
        background-color: #f0f2f6;
    }
</style>
""", unsafe_allow_html=True)

st.title("🤖 أداة استخلاص التقارير المالية الآلية (SQLite)")
st.caption("تم تحديث الكود الآن لدعم ملفات PDF عبر تحويلها إلى صور.")

# تهيئة الاتصال بالقاعدة والتأكد من وجود الجدول
db_conn = get_db_connection()
if db_conn:
    init_db(db_conn)
else:
    st.error("تعذر تهيئة قاعدة البيانات. يرجى التحقق من الأذونات.")

# قسم تحميل الملف
uploaded_file = st.file_uploader("قم بتحميل ملف PDF أو صورة للتقرير المالي:", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None:
    # قراءة محتويات الملف كبايتات
    file_bytes = uploaded_file.read()
    file_name = uploaded_file.name
    file_type = file_name.split('.')[-1].lower() # استخراج نوع الملف
    
    # رسالة لتبدأ عملية الاستخلاص مباشرة بعد التحميل
    st.markdown(f"**تم تحميل الملف:** `{file_name}`")
    
    # تشغيل وظيفة الاستخلاص والحفظ مباشرة
    with st.spinner("جاري تحليل وحفظ التقرير..."):
        extract_financial_data(file_bytes, file_name, file_type)


st.subheader("سجل التقارير الموحد والمحفوظ (SQLite)")

# عرض البيانات المحفوظة
reports_data = fetch_all_reports(db_conn)
if reports_data:
    df_reports = pd.DataFrame(reports_data)
    
    # استبعاد الأعمدة الخاصة بالقاعدة 'id' و 'extraction_timestamp'
    display_columns = ['file_name'] + REPORT_FIELDS
    
    # دالة بسيطة لترجمة رؤوس الأعمدة للعرض
    arabic_headers = {
        "file_name": "اسم الملف",
        "issue_number": "رقم الصادر", "issue_date": "تاريخ الصادر", "suspect_name": "اسم المشتبه به", 
        "id_number": "رقم الهوية", "nationality": "الجنسية", "birth_date": "تاريخ الميلاد", 
        "entry_date": "تاريخ الدخول", "social_status": "الحالة الاجتماعية", 
        "profession": "المهنة", "phone_number": "رقم الجوال", "city": "المدينة", 
        "account_balance": "رصيد الحساب", "annual_income": "الدخل السنوي", 
        "incoming_number": "رقم الوارد", "incoming_date": "تاريخ الوارد", 
        "employer_id": "رقم السجل التجاري", "suspicion_reason": "سبب الاشتباه", 
        "study_start_date": "تاريخ بداية الدراسة", "study_end_date": "تاريخ نهاية الدراسة", 
        "total_deposit_during_study": "إجمالي الإيداع أثناء الدراسة"
    }
    
    # تجهيز إطار البيانات للعرض
    df_display = df_reports[display_columns].rename(columns=arabic_headers)
    
    # عرض الجدول
    st.dataframe(df_display, use_container_width=True)
    
    # زر تنزيل البيانات
    csv_data = df_display.to_csv(index=False, encoding='utf-8-sig')
    st.download_button(
        label="📥 تنزيل جميع البيانات (CSV)",
        data=csv_data,
        file_name='extracted_financial_reports.csv',
        mime='text/csv'
    )
else:
    st.info("لا توجد تقارير محفوظة حالياً في قاعدة بيانات SQLite.")
