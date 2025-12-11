# db.py
import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql
import pandas as pd
import re
from itertools import permutations
import datetime

# ===============================
# إعدادات وثوابت
# ===============================

try:
    from hijri_converter import Hijri
except ImportError:
    Hijri = None
    st.warning("⚠️ مكتبة 'hijri-converter' غير موجودة. لن يتم دعم تحويل التواريخ الهجرية.")

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأعمدة النهائية في قاعدة البيانات
DB_COLUMN_NAMES = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة",
    "رقم الدلالة", # سيتم التعامل معه كسلسلة نصية (TEXT)
    "اسم الملف",
    "وقت الاستخلاص"
]

DATA_KEYS = DB_COLUMN_NAMES

# ===============================
# دوال الاتصال والتحويل
# ===============================

def arabic_to_english_numbers(text):
    """تحويل الأرقام العربية إلى إنجليزية لتسهيل المعالجة."""
    if not isinstance(text, str):
        return str(text) 
    
    arabic_map = {
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
        '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
        '،': '.' 
    }
    return text.translate(str.maketrans(arabic_map))


def connect_db():
    """ينشئ اتصالًا بقاعدة البيانات."""
    try:
        if not DB_URL:
            st.error("❌ متغير البيئة 'DATABASE_URL' غير موجود.")
            return None
        conn = psycopg2.connect(DB_URL, sslmode='require')
        return conn
    except Exception as e:
        st.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        return None

def _convert_hijri_to_date(parts_tuple):
    """
    دالة مساعدة: تحاول تحويل جزء من التاريخ (المفترض أنه سنة، شهر، يوم) إلى تاريخ ميلادي.
    """
    if not Hijri or len(parts_tuple) != 3:
        return None
        
    try:
        y_str, m_str, d_str = [re.sub(r'[^\d]', '', p) for p in parts_tuple]
        y, m, d = int(y_str), int(m_str), int(d_str)
    except ValueError:
        return None

    if y < 1000:
        if y < 60: # 14xx
            y += 1400
        else: # 13xx
            y += 1300
    
    
    if 1300 < y < 1500:
        if 1 <= m <= 12 and 1 <= d <= 30:
            try:
                # التحقق من صلاحية التاريخ الهجري قبل التحويل
                gregorian_date = Hijri(y, m, d).to_gregorian()
                return gregorian_date
            except Exception:
                return None
                
    return None

def clean_data_type(key, value):
    """تنظيف وتحويل القيم إلى تنسيقات صالحة لـ PostgreSQL."""
    
    # 1. التعامل مع القيم الفارغة
    if value is None or str(value).strip() in ['غير متوفر', '', 'nan']:
        return None

    value = arabic_to_english_numbers(str(value))

   
    numeric_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    
    if key in numeric_fields: 
        try:
            
           
            temp_val = re.sub(r'[^\d\.-]', '', value.replace(',', ''))
            
            if not temp_val:
                return None
                
            if temp_val.count('.') > 1:
                temp_val = temp_val.replace('.', '')
                
            return float(temp_val)

        except ValueError:
            return None
            
    # 3. تحويل الأعمدة التاريخية (DATE)
    date_fields = ["تاريخ الصادر", "تاريخ الميلاد الوافد", "تاريخ الدخول", "تاريخ الوارد", "تاريخ الدارسة من", "تاريخ الدراسة الى"]
    
    if key in date_fields:
        date_str = value
        clean_str_base = re.sub(r'[^\d/\-.]', '', date_str).strip()
        
        parts = [p for p in re.split(r'[/\-.]', clean_str_base) if p.strip()] 
        if len(parts) != 3:
            return None

        
        try:
            date_obj = pd.to_datetime(clean_str_base, errors='coerce', dayfirst=True) 
            if pd.notna(date_obj):
                if date_obj.year > 1900 and date_obj.year <= datetime.date.today().year:
                    return date_obj.date()
        except Exception:
            pass

        # ب. محاولة التحويل الهجري
        if Hijri:
            try:
                possible_orders = set(permutations(parts))

                for p in possible_orders:
                    result = _convert_hijri_to_date(p)
                    if result:
                     
                        if result.year > 1900 and result.year <= datetime.date.today().year:
                             return result
            except Exception:
                pass 
            
        return None

    # 4. القيم الأخرى (VARCHAR/TEXT/TIMESTAMP)
    return value


# ===============================
# دوال العمليات على قاعدة البيانات
# ===============================


def save_to_db(extracted_data):
    """يحفظ البيانات المستخلصة إلى جدول تقارير_الاشتباه."""
    conn = connect_db()
    if not conn:
        return False
        
    processed_data_for_display = {}
    insert_columns = []
    insert_values = []
    
    for key in DATA_KEYS:
        value = extracted_data.get(key)
        processed_value = clean_data_type(key, value)
        
       
        insert_columns.append(sql.Identifier(key))
        insert_values.append(processed_value)

 

    
    try:
        cur = conn.cursor()
        
        columns_sql = sql.SQL(', ').join(insert_columns)
        
        insert_query = sql.SQL("""
            INSERT INTO public.تقارير_الاشتباه ({columns})
            VALUES ({values})
        """).format(
            columns=columns_sql,
            values=sql.SQL(', ').join(sql.Placeholder() * len(insert_values)) 
        )
        
        cur.execute(insert_query, insert_values)
        
        conn.commit()
        cur.close()
        conn.close()
        
        # الرسالة المطلوبة بعد الحفظ الناجح
        st.success("✅ تم حفظ البيانات بنجاح في قاعدة البيانات.") 
        return True
        
    except Exception as e:
        # (باقي منطق معالجة الأخطاء كما هو)
        error_msg = str(e)
        if 'column "رقم الدلالة" is of type integer but expression is of type text' in error_msg:
             st.error("💡 ملاحظة مهمة: عمود **'رقم الدلالة'** في قاعدة البيانات يجب أن يكون بنوع **TEXT** لكي يقبل قيمة مثل '1,11'.")
             st.error("لحل المشكلة نهائياً، يرجى تشغيل الأمر التالي في PgAdmin أو أداة إدارة قاعدة البيانات الخاصة بك:")
             st.code("""
             ALTER TABLE public.تقارير_الاشتباه
             ALTER COLUMN "رقم الدلالة" TYPE TEXT;
             """)
        elif 'column "وقت الاستخلاص" is of type timestamp without time zone but expression is of type text' in error_msg:
             st.error("💡 ملاحظة: تأكد أن عمود **'وقت الاستخلاص'** في جدول PostgreSQL بنوع **TIMESTAMP**.")
        elif 'column "رصيد الحساب" is of type numeric but expression is of type text' in error_msg:
             st.error("💡 ملاحظة: تأكد أن عمود **'رصيد الحساب'** وعمود **'الدخل السنوي'** و **'إجمالي إيداع الدراسة'** في جدول PostgreSQL بنوع **NUMERIC**.")
        elif 'invalid input syntax for type date' in error_msg:
             st.error("💡 ملاحظة: فشل تحويل أحد التواريخ إلى صيغة `YYYY-MM-DD`. تأكد من أن الأعمدة التاريخية في PostgreSQL هي بنوع **DATE**.")
        
        st.error(f"❌ فشل الحفظ في قاعدة البيانات: {e}")
        
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
        
        # اختيار جميع الأعمدة المعرفة فقط
        select_columns = sql.SQL(', ').join([sql.Identifier(col) for col in DB_COLUMN_NAMES])

        select_query = sql.SQL('SELECT {columns} FROM public.تقارير_الاشتباه').format(columns=select_columns)
        
        cur.execute(select_query)
        
        column_names = DB_COLUMN_NAMES
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return records, column_names

    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء جلب البيانات من قاعدة البيانات: {e}")
        if conn:
            conn.close()
        return None, None


def initialize_db():
    """ينشئ جدول تقارير_الاشتباه إذا لم يكن موجودًا بالفعل. تم تحديث نوع رقم الدلالة إلى TEXT."""
    conn = connect_db()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.تقارير_الاشتباه (
                "رقم الصادر" TEXT,
                "تاريخ الصادر" DATE,
                "اسم المشتبه به" TEXT,
                "رقم الهوية" TEXT,
                "الجنسية" TEXT,
                "تاريخ الميلاد الوافد" DATE,
                "تاريخ الدخول" DATE,
                "الحالة الاجتماعية" TEXT,
                "المهنة" TEXT,
                "رقم الجوال" TEXT,
                "المدينة" TEXT,
                "رصيد الحساب" NUMERIC,
                "الدخل السنوي" NUMERIC,
                "رقم الوارد" TEXT,
                "تاريخ الوارد" DATE,
                "رقم صاحب العمل/ السجل التجاري" TEXT,
                "سبب الاشتباه" TEXT,
                "تاريخ الدارسة من" DATE,
                "تاريخ الدراسة الى" DATE,
                "إجمالي إيداع الدراسة" NUMERIC,
                "رقم الدلالة" TEXT,
                "اسم الملف" TEXT,
                "وقت الاستخلاص" TIMESTAMP
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        st.error(f"❌ خطأ أثناء إنشاء الجدول: {e}")
        return False
