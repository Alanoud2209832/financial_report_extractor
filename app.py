import streamlit as st
import pandas as pd
import json
import io
import time
import sqlite3
import fitz # PyMuPDF library for PDF processing
from PIL import Image # Pillow library for image handling
from google import genai
from google.genai.errors import APIError
import base64

# ----------------------------------------------------------------
# 1. API Settings, Arabic Texts, and Database Initialization
# ----------------------------------------------------------------

# 🚨 IMPORTANT: Set your API Key here!
# Please replace the following placeholder with your valid Gemini API Key
GEMINI_API_KEY = "AIzaSyBVJvH_Z5AX9dwXR7UFhbeo9iB5-aL-rZI" # ⬅️ Please paste your valid key here

# Gemini Model Configuration
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    "أنت خبير في تحليل التقارير المالية. مهمتك هي قراءة النص والصورة المستخرجة من وثيقة "
    "مالية وتحويله إلى كائن JSON وفقًا للمخطط المحدد. يجب أن تكون دقيقًا جدًا في "
    "استخلاص القيم وأن تتأكد من مطابقتها لأسماء الحقول المطلوبة باللغة الإنجليزية. "
    "استخدم القيمة 'N/A' للحقول غير الموجودة."
)

# Required Fields (English keys for JSON stability) and their Arabic equivalent for display
REPORT_FIELD_MAP = {
    "issue_number": "رقم الصادر",
    "issue_date": "تاريخ الصادر",
    "suspect_name": "اسم المشتبه به",
    "id_number": "رقم الهوية",
    "nationality": "الجنسية",
    "birth_date": "تاريخ الميلاد",
    "entry_date": "تاريخ الدخول",
    "social_status": "الحالة الاجتماعية",
    "profession": "المهنة",
    "phone_number": "رقم الجوال",
    "city": "المدينة",
    "account_balance": "رصيد الحساب",
    "annual_income": "الدخل السنوي",
    "incoming_number": "رقم الوارد",
    "incoming_date": "تاريخ الوارد",
    "employer_id": "رقم السجل التجاري لصاحب العمل",
    "suspicion_reason": "سبب الاشتباه",
    "study_start_date": "تاريخ بداية الدراسة",
    "study_end_date": "تاريخ نهاية الدراسة",
    "total_deposit_during_study": "إجمالي الإيداع أثناء الدراسة"
}
REPORT_FIELDS = list(REPORT_FIELD_MAP.keys())

# Response Schema for Gemini (JSON Schema) - including Arabic description
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        field: {
            "type": "STRING", 
            "description": f"القيمة المستخلصة لـ: {REPORT_FIELD_MAP[field]}"
        } for field in REPORT_FIELDS
    },
    "propertyOrdering": REPORT_FIELDS
}

# ----------------------------------------------------------------
# 2. SQLite Functions (Persistent Storage)
# ----------------------------------------------------------------

DB_FILE = 'financial_data.db'

# Create database connection
@st.cache_resource
def get_db_connection():
    """Establishes an SQLite database connection."""
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        st.error(f"خطأ في الاتصال بقاعدة البيانات SQLite: {e}")
        return None

# Initialize the database table
def init_db(conn):
    """Creates the 'reports' table if it doesn't exist."""
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

# Fetch all data from the database
def fetch_all_reports(conn):
    """Fetches all records from the 'reports' table."""
    if conn:
        try:
            reports = conn.execute("SELECT * FROM reports ORDER BY extraction_timestamp DESC").fetchall()
            return [dict(report) for report in reports]
        except sqlite3.Error as e:
            st.error(f"خطأ في جلب التقارير: {e}")
            return []
    return []

# Insert a new report into the database
def insert_report(conn, data):
    """Inserts the extracted report data into the database."""
    if conn:
        try:
            # Ensure all required fields and metadata are present
            data_to_insert = {field: data.get(field, 'N/A') for field in REPORT_FIELDS}
            data_to_insert['file_name'] = data.get('file_name', 'N/A')
            data_to_insert['extraction_timestamp'] = data.get('extraction_timestamp', pd.Timestamp.now().isoformat())

            columns = ', '.join(data_to_insert.keys())
            placeholders = ', '.join('?' * len(data_to_insert))
            values = tuple(data_to_insert.values())
            
            conn.execute(f"INSERT INTO reports ({columns}) VALUES ({placeholders})", values)
            conn.commit()
            return True
        except sqlite3.Error as e:
            st.error(f"خطأ في حفظ التقرير في قاعدة البيانات: {e}")
            return False
    return False

# ----------------------------------------------------------------
# 3. File Processing and Extraction Function
# ----------------------------------------------------------------

def convert_pdf_to_images(file_bytes):
    """Converts a PDF file (as bytes) to a list of PNG image bytes."""
    try:
        # Check if fitz (PyMuPDF) is available
        if 'fitz' not in globals():
             st.error("خطأ: مكتبة PyMuPDF (fitz) غير مثبتة. الرجاء تثبيتها باستخدام الأمر: pip3 install PyMuPDF")
             return []

        pdf_document = fitz.open(stream=file_bytes, filetype="pdf")
        
        # Target the first page only
        page = pdf_document.load_page(0)
        
        # Create a high-resolution pixel map (zoom 3.0 for better text clarity)
        matrix = fitz.Matrix(3.0, 3.0)
        pix = page.get_pixmap(matrix=matrix)
        
        # Convert pixel data to raw bytes for sending
        img_bytes = pix.tobytes(output='png')
        
        return [img_bytes]
    except Exception as e:
        st.error(f"حدث خطأ أثناء تحويل PDF إلى صورة: {e}. قد يكون السبب هو عدم التثبيت الصحيح لمكتبة PyMuPDF.")
        return []

def extract_financial_data(file_bytes, file_name, file_type):
    """
    Receives file data and uses the Gemini API to extract financial data
    and insert it directly into the database.
    """
    if not GEMINI_API_KEY:
        st.error("الرجاء تحديث 'GEMINI_API_KEY' في الكود بمفتاح صالح قبل تحميل الملف.")
        return False
        
    response = None
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # 1. Define Multimodal Content
        content_parts = [
            "قم باستخلاص جميع البيانات من هذه الوثيقة المالية "
            "وحوّلها إلى كائن JSON يطابق المخطط المحدد بدقة. "
            "يرجى استخدام الحقول الإنجليزية (issue_number, etc.) كمفاتيح JSON. "
            "إذا لم تتمكن من العثور على قيمة حقل معين، استخدم 'N/A'."
        ]
        
        if file_type == 'pdf':
            st.info("تم الكشف عن ملف PDF. جاري تحويل الصفحة الأولى إلى صورة...")
            image_bytes_list = convert_pdf_to_images(file_bytes)
            
            if not image_bytes_list:
                return False # Conversion failed
                
            # Add image bytes to the request content
            for img_bytes in image_bytes_list:
                content_parts.append({
                    "inlineData": {
                        "data": base64.b64encode(img_bytes).decode('utf-8'), # Base64 encoding for API call
                        "mimeType": "image/png"
                    }
                })
        
        elif file_type in ['png', 'jpg', 'jpeg']:
            # Add the original image directly
            content_parts.append({
                "inlineData": {
                    "data": base64.b64encode(file_bytes).decode('utf-8'),
                    "mimeType": f"image/{file_type}" 
                }
            })
        else:
            st.error(f"نوع الملف غير مدعوم: {file_type}")
            return False

        # 2. Generation Configuration
        config = {
            "systemInstruction": SYSTEM_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        }

        # 3. Request Content Generation
        st.info(f"جاري استخلاص البيانات من '{file_name}'...")
        
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=content_parts,
            config=config,
        )

        # 4. Process Response and Save to SQLite
        json_output = response.text
        extracted_data = json.loads(json_output)
        
        # Add basic data
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
        st.error(f"خطأ في الاتصال بـ Gemini API. تأكدي من صحة المفتاح. الخطأ: {e}")
    except json.JSONDecodeError:
        st.error(f"فشل في تفسير استجابة النموذج كـ JSON. (الاستجابة: {json_output if 'json_output' in locals() else 'N/A'})")
    except Exception as e:
        st.error(f"حدث خطأ غير متوقع: {e}")
        if response and response.text:
            st.code(response.text)
    return False

# ----------------------------------------------------------------
# 4. User Interface (Streamlit UI)
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

st.title("🤖 أداة استخلاص التقارير المالية الآلية (تخزين SQLite/تنزيل Excel)")
st.caption("النسخة الحالية تستخلص البيانات المطلوبة بالكامل وتخزنها محلياً.")

# Initialize DB connection and table
db_conn = get_db_connection()
if db_conn:
    init_db(db_conn)
else:
    st.error("تعذر تهيئة قاعدة البيانات. لن يتم حفظ التقارير.")

# File Upload Section
uploaded_file = st.file_uploader("قم بتحميل ملف PDF أو صورة للتقرير المالي:", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None:
    # Read file contents as bytes
    file_bytes = uploaded_file.read()
    file_name = uploaded_file.name
    file_type = file_name.split('.')[-1].lower()
    
    st.markdown(f"**تم تحميل الملف:** `{file_name}`")
    
    # Run extraction and saving immediately
    if st.button("بدء الاستخلاص والتحليل"):
        with st.spinner("جاري تحليل وحفظ التقرير..."):
            extract_financial_data(file_bytes, file_name, file_type)


st.subheader("سجل التقارير الموحد والمحفوظ (SQLite)")

# Display saved data
reports_data = fetch_all_reports(db_conn)
if reports_data:
    df_reports = pd.DataFrame(reports_data)
    
    # Select columns for display and use Arabic headers
    display_columns = ['file_name'] + REPORT_FIELDS
    
    # Prepare DataFrame for display with translated headers
    df_display = df_reports[display_columns].rename(columns=REPORT_FIELD_MAP)
    
    # Display the table
    st.dataframe(df_display, use_container_width=True)
    
    # Download Button (CSV/Excel)
    csv_data = df_display.to_csv(index=False, encoding='utf-8-sig')
    st.download_button(
        label="📥 تنزيل جميع البيانات (ملف Excel - CSV)",
        data=csv_data,
        file_name='extracted_financial_reports.csv',
        mime='text/csv'
    )
else:
    st.info("لا توجد تقارير محفوظة حالياً في قاعدة بيانات SQLite.")
