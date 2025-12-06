# db.py
import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql 
import pandas as pd 
import re 

# محاولة استيراد مكتبة التحويل الهجري
try:
    from hijri_converter import Hijri
except ImportError:
    st.warning("⚠️ مكتبة hijri-converter غير مثبتة. التواريخ الهجرية قد لا يتم تحويلها بشكل صحيح.")
    Hijri = None 

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأعمدة النهائية في قاعدة البيانات (لا تتضمن "مؤشر التشتت")
DB_COLUMN_NAMES = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة",
    "اسم الملف", 
    "وقت الاستخلاص"
]

DATA_KEYS = DB_COLUMN_NAMES 

# دالة مساعدة لتحويل الأرقام العربية إلى إنجليزية
def arabic_to_english_numbers(text):
    if not isinstance(text, str):
        return text
    
    arabic_map = {
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
        '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
    }
    return text.translate(str.maketrans(arabic_map))


def connect_db():
    """ينشئ اتصالًا بقاعدة البيانات."""
    try:
        if not DB_URL:
            st.error("❌ متغير DATABASE_URL غير موجود. يرجى مراجعة ملف .env")
            return None
        conn = psycopg2.connect(DB_URL, sslmode='require') 
        return conn
    except Exception as e:
        st.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}") 
        return None


def clean_data_type(key, value):
    """تنظيف وتحويل القيم إلى تنسيقات صالحة لـ PostgreSQL."""
    
    # 1. التعامل مع القيم الفارغة
    if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
        return None

    # 2. تحويل الأعمدة الرقمية (NUMERIC)
    numeric_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    if key in numeric_fields:
        try:
            # 1. تحويل الأرقام العربية
            cleaned_value = arabic_to_english_numbers(str(value)) 
            
            # 2. إزالة النص غير الرقمي (مثل 'ريال') قبل التعامل مع الفواصل
            cleaned_value = re.sub(r'[^\d\.,-]', '', cleaned_value)
            
            # 3. إزالة فواصل الألوف (العربية والإنجليزية)
            # مثال: '293,436' تصبح '293436' (مما يحل مشكلة القراءة الخاطئة)
            cleaned_value = cleaned_value.replace('،', '').replace(',', '')
            
            # 4. التنظيف النهائي (يجب أن يبقى فقط الأرقام والنقطة العشرية)
            cleaned_value = re.sub(r'[^\d\.]', '', cleaned_value)
            
            # 5. معالجة النقاط العشرية المتعددة
            if cleaned_value.count('.') > 1:
                 parts = cleaned_value.split('.')
                 cleaned_value = "".join(parts[:-1]) + "." + parts[-1]

            return float(cleaned_value)
        except ValueError:
            return None
            
    # 3. تحويل الأعمدة التاريخية (DATE)
    date_fields = ["تاريخ الصادر", "تاريخ الميلاد الوافد", "تاريخ الدخول", "تاريخ الوارد", "تاريخ الدارسة من", "تاريخ الدراسة الى"]
    if key in date_fields:
        
        # تحويل الأرقام العربية في التاريخ إلى إنجليزية
        date_str = arabic_to_english_numbers(str(value))
        
        # أ. محاولة تحويل ميلادي مباشر
        try:
            date_obj = pd.to_datetime(date_str, errors='coerce', dayfirst=False)
            if pd.notna(date_obj):
                return date_obj.date()
        except Exception:
            pass
        
        # ب. محاولة التحويل الهجري
        if Hijri:
            try:
                clean_str = date_str.replace('م', '').strip()
                
                # استخدام re.split لتقسيم النص بأي فاصل من الفواصل الشائعة (/, -, .)
                parts = re.split(r'[/\-.]', clean_str)
                
                if len(parts) == 3:
                    y, m, d = [int(re.sub(r'[^\d]', '', p)) for p in parts]
                    
                    # 💡 معالجة الأخطاء الشائعة في قراءة سنة ١٤٤x (حل مشكلة ٠٩٤٥)
                    # 1. إذا كانت السنة 4xx (المشكلة الشائعة بحذف '1')
                    if y >= 400 and y <= 500:
                        y += 1000 # (مثال: 445 -> 1445)
                    # 2. إذا كانت السنة 9xx (المشكلة التي أبلغ عنها المستخدم: 0945 بدلاً من 1445)
                    elif y >= 900 and y <= 999:
                        # نفترض أن الخطأ في قراءة القرن، ونحوله إلى القرن الحالي (14xx)
                        y = 1400 + (y % 100) # (مثال: 945 -> 1400 + 45 = 1445)
                        
                    
                    if y > 1300 and y < 1500: 
                        gregorian_date = Hijri(y, m, d).to_gregorian()
                        return gregorian_date.date()
                    
            except Exception:
                 pass

        return None

    # 4. القيم الأخرى (VARCHAR/TEXT)
    return value


def save_to_db(extracted_data):
    """يحفظ البيانات المستخلصة إلى جدول تقارير_الاشتباه."""
    conn = connect_db()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        insert_columns = []
        insert_values = []
        
        # نستخدم DATA_KEYS (التي هي DB_COLUMN_NAMES) لضمان عدم محاولة إدخال 'مؤشر التشتت'
        for key in DATA_KEYS:
            value = extracted_data.get(key)
            
            processed_value = clean_data_type(key, value)

            insert_columns.append(sql.Identifier(key))
            insert_values.append(sql.Literal(processed_value))
            

        columns_sql = sql.SQL(', ').join(insert_columns)
        values_list = sql.SQL(', ').join(insert_values)

        insert_query = sql.SQL("""
            INSERT INTO public.تقارير_الاشتباه ({columns})
            VALUES ({values})
        """).format(
            columns=columns_sql,
            values=values_list
        )
        
        cur.execute(insert_query)
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        # هذه الرسالة ستظهر الآن بوضوح لأن app.py سيتوقف عند أول خطأ حفظ
        st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False

def fetch_all_reports():
    """يجلب جميع السجلات من جدول تقارير_الاشتباه."""
    conn = connect_db()
    if not conn:
        return None, None 

    try:
        cur = conn.cursor()
        
        select_query = sql.SQL('SELECT * FROM public.تقارير_الاشتباه')
        
        cur.execute(select_query)
        
        column_names = [desc[0] for desc in cur.description]
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return records, column_names

    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء جلب البيانات من قاعدة البيانات: {e}")
        if conn:
            conn.close()
        return None, None
