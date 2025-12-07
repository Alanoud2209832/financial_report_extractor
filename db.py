# db.py
import psycopg2
import os
from dotenv import load_dotenv
import streamlit as st
from psycopg2 import sql
import pandas as pd
import re
from itertools import permutations # تم إضافة مكتبة التباديل لزيادة مرونة قراءة التاريخ

# محاولة استيراد مكتبة التحويل الهجري
try:
    from hijri_converter import Hijri
except ImportError:
    st.warning("⚠️ مكتبة hijri-converter غير متوفرة. التواريخ الهجرية قد لا يتم تحويلها بشكل صحيح. يرجى تثبيتها عبر 'pip install hijri-converter'.")
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

def _convert_hijri_to_date(parts_tuple):
    """
    دالة مساعدة: تحاول تحويل جزء من التاريخ (المفترض أنه سنة، شهر، يوم) إلى تاريخ ميلادي.
    """
    if not Hijri or len(parts_tuple) != 3:
        return None
        
    try:
        # تحويل الأجزاء إلى أرقام، وإزالة أي رموز غير رقمية
        # نفترض أن الترتيب الحالي للـ tuple هو (Y, M, D)
        y_str, m_str, d_str = [re.sub(r'[^\d]', '', p) for p in parts_tuple]
        y, m, d = int(y_str), int(m_str), int(d_str)
    except ValueError:
        return None

    # معالجة الأخطاء الشائعة في قراءة السنة الهجرية (مثل تحويل 445 إلى 1445)
    if y < 1000 and y >= 400:
        y += 1000 
    # في حال استخلاص رقمين للسنة فقط (مثل 22)
    elif y >= 1 and y <= 99:
        y += 1400
    
    # تحقق من نطاق السنة الهجرية المعقول
    if y > 1300 and y < 1500:
        # تحقق بسيط من نطاق الشهر واليوم قبل استخدام المكتبة
        if 1 <= m <= 12 and 1 <= d <= 30:
            try:
                gregorian_date = Hijri(y, m, d).to_gregorian()
                return gregorian_date.date()
            except Exception:
                # قد تفشل المكتبة لأسباب مثل يوم 30 في شهر لا يحتمله
                return None
                
    return None

def clean_data_type(key, value):
    """تنظيف وتحويل القيم إلى تنسيقات صالحة لـ PostgreSQL."""
    
    # 1. التعامل مع القيم الفارغة
    if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
        return None

    # 2. تحويل الأعمدة الرقمية (NUMERIC) - (المنطق سليم وتم إبقاؤه)
    numeric_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    if key in numeric_fields:
        try:
            cleaned_value = arabic_to_english_numbers(str(value))
            temp_val = re.sub(r'[^\d\.,-]', '', cleaned_value)

            last_separator_index = max(temp_val.rfind('.'), temp_val.rfind(','))
            
            if last_separator_index != -1:
                integer_part = temp_val[:last_separator_index]
                decimal_part = temp_val[last_separator_index+1:]
                
                integer_part = re.sub(r'[,\.]', '', integer_part) 
                
                if len(decimal_part) > 2:
                    final_val = integer_part + decimal_part
                    final_val = re.sub(r'[^\d\.-]', '', final_val)
                    return float(final_val)
                else:
                    final_val = f"{integer_part}.{decimal_part}"
                    final_val = re.sub(r'[^\d\.-]', '', final_val)
                    return float(final_val)
            else:
                final_val = re.sub(r'[^\d\.-]', '', temp_val)
                if not final_val:
                    return None
                return float(final_val)

        except ValueError:
            return None
            
    # 3. تحويل الأعمدة التاريخية (DATE)
    date_fields = ["تاريخ الصادر", "تاريخ الميلاد الوافد", "تاريخ الدخول", "تاريخ الوارد", "تاريخ الدارسة من", "تاريخ الدراسة الى"]
    if key in date_fields:
        
        date_str = arabic_to_english_numbers(str(value))
        clean_str_base = re.sub(r'[^\d/\-.]', '', date_str).strip()
        
        # أ. محاولة تحويل ميلادي مباشر
        try:
            date_obj = pd.to_datetime(clean_str_base, errors='coerce', dayfirst=False)
            if pd.notna(date_obj) and date_obj.year > 1800:
                return date_obj.date()
        except Exception:
            pass
        
        # ب. محاولة التحويل الهجري (التعديل الرئيسي)
        if Hijri:
            try:
                parts = [p for p in re.split(r'[/\-.]', clean_str_base) if p.strip()] 
                
                if len(parts) == 3:
                    
                    # 💡 تجربة جميع الترتيبات الستة المحتملة (Y, M, D)
                    # نستخدم مجموعة (set) لضمان فريدة الترتيبات
                    possible_orders = set(permutations(parts))

                    for p in possible_orders:
                        # يتم تمرير الترتيب p على أساس (سنة، شهر، يوم) ليتم التحقق منه داخل الدالة المساعدة
                        result = _convert_hijri_to_date(p)
                        if result:
                            return result
                            
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
