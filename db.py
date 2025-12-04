# db.py
import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql 
import pandas as pd 
import re # لإزالة الأحرف غير الرقمية

# محاولة استيراد مكتبة التحويل الهجري (مهمة لحل مشكلتك)
try:
    from hijri_converter import Hijri
except ImportError:
    # سيظهر هذا التحذير إذا لم يتم تثبيت hijri-converter
    st.warning("⚠️ مكتبة hijri-converter غير مثبتة. التواريخ الهجرية قد لا يتم تحويلها بشكل صحيح.")
    Hijri = None 

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأسماء الحقيقية للأعمدة في قاعدة البيانات (يجب أن تطابق الأعمدة في PostgreSQL)
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
    # استخدام ترجمة النص لتحويل الأرقام العربية
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
    
    # 1. التعامل مع القيم الفارغة (يجب أن تكون أولاً)
    if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
        return None

    # 2. تحويل الأعمدة الرقمية (NUMERIC)
    numeric_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    if key in numeric_fields:
        try:
            # تحويل الأرقام العربية إلى إنجليزية قبل معالجتها كرقم
            cleaned_value = arabic_to_english_numbers(str(value)) 
            
            # إزالة أي أحرف غير رقمية أو علامات عشرية غير ضرورية
            cleaned_value = cleaned_value.replace('،', '').replace(',', '')
            cleaned_value = re.sub(r'[^\d\.]', '', cleaned_value)
            
            return float(cleaned_value)
        except ValueError:
            st.warning(f"⚠️ تنبيه: فشل تحويل القيمة '{value}' في حقل '{key}' إلى رقم.")
            return None
            
    # 3. تحويل الأعمدة التاريخية (DATE) - الجزء الذي يحل مشكلتك
    date_fields = ["تاريخ الصادر", "تاريخ الميلاد الوافد", "تاريخ الدخول", "تاريخ الوارد", "تاريخ الدارسة من", "تاريخ الدراسة الى"]
    if key in date_fields:
        
        # 💡 الخطوة الجديدة: تحويل الأرقام العربية في التاريخ إلى إنجليزية
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
                parts = clean_str.split('/')
                
                if len(parts) == 3:
                    y, m, d = [int(re.sub(r'[^\d]', '', p)) for p in parts]
                    
                    # معالجة السنة غير المكتملة (445 -> 1445)
                    if y >= 400 and y < 1000:
                        y += 1000  
                    
                    if y > 1300 and y < 1500: 
                        gregorian_date = Hijri(y, m, d).to_gregorian()
                        return gregorian_date.date()
                    
            except Exception:
                pass

        st.warning(f"⚠️ تنبيه: فشل تحويل القيمة '{value}' في حقل '{key}' إلى تاريخ.")
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
        
        for key in DATA_KEYS:
            value = extracted_data.get(key)
            
            # 💡 تنظيف وتحويل القيمة
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
