# -*- coding: utf-8 -*-
# app.py
import streamlit as st
import pandas as pd
import json
import io
import base64
import os
import re 
import pytz 
from google import genai
from google.genai.errors import APIError
import time 
from db import save_to_db, fetch_all_reports

# ===============================
# 1. إعدادات API
# ===============================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCH82HGwbNJxqjABAARHoi1lQfPoYL_j1I") 
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'

REPORT_FIELDS_ARABIC = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة",
    "رقم الدلالة"  # 💡 تم إضافة الحقل الجديد هنا
]

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        field: {"type": "STRING", "description": f"القيمة المستخلصة لـ: {field}"}
        for field in REPORT_FIELDS_ARABIC
    },
    "propertyOrdering": REPORT_FIELDS_ARABIC
}

DELALAT_MAPPING = {
    1: "تكرار العمليات المالية (إيداعات، حوالات سحوبات مشتريات) في حساب المقيم لا تتناسب مع دخله السنوي.",
    2: "تحويلات أو إيداعات نقدية من حساب عميل مقيم الى حساب فرد سعودي أو كيان تجاري.",
    3: "حوالات صادرة أو عمليات مالية متنوعة من حساب مقيم أجنبي لعمليات سداد مصروفات تنم عن المتاجرة فيها أو إعادة بيعها.",
    4: "حوالات دولية صادرة من حساب فرد سعودي أو حساب كيان تجاري إلى حسابات أشخاص بشكل متكرر لا تربطهم به غرض أو علاقة عمل.",
    5: "مقيم يقوم بتنفيذ عمليات تحويل مالية خارج المملكة له أو لأشخاص آخرين بمبالغ لا تتناسب مع دخله وقد يكون مصدرها إيداعات نقدية من عدة عملاء مقيمين.",
    6: "حوالات دولية واردة للحساب الشخصي للمقيم أو للبطاقات الائتمانية بمبالغ عالية تنم عن إدارة نشاط تجاري داخل المملكة.",
    7: "شخص مقيم يقوم بتنفيذ عمليات مالية (إيداع شيك أو صرف شيك أو استقبال حواله مالية) وليس لديه حساب بنكي (عميل عابر).",
    8: "إيداعات نقدية في حساب كيان تجاري بشكل متكرر أو إيداعات مبيعات نقاط بيع، يليها تنفيذ حوالات خارجية أو داخلية لعدة عملاء مقيمين أو عمليات سحب.",
    9: "حوالات دولية واردة أو صادرة لحساب الكيان التجاري لا تتناسب مع نشاط الكيان التجاري.",
    10: "تفويض أجنبي على حساب بنكي عائد لكيان تجاري وتمكينه من الحساب بشكل كامل دون وجود مبرر أو غرض واضح.",
    11: "فتح عدة حسابات الفروع كيان تجاري لنفس النشاط دون وجود ارتباط واضح بين هذه الحسابات، نظراً لإدارة الحساب الخاص بالفرع من قبل المقيم."
}

SYSTEM_PROMPT = (
    "أنت نظام استخلاص بيانات آلي (OCR/NLP). مهمتك هي قراءة النص والصورة المستخرجة من الوثيقة المالية "
    "وتحويل البيانات إلى كائن JSON وفقاً للمخطط المحدد بدقة. "
    "يجب عليك استخلاص جميع التواريخ الهجرية والميلادية وتحويلها إلى **صيغة رقمية موحدة** 'السنة/الشهر/اليوم' (YYYY/MM/DD) مثل '1445/06/21'. "
    "هذا التنسيق مطلوب لجميع حقول التاريخ التالية: 'تاريخ الصادر', 'تاريخ الوارد', 'تاريخ الميلاد الوافد', 'تاريخ الدخول', 'تاريخ الدارسة من', و 'تاريخ الدراسة الى'. "
    "قم بنسخ جميع القيم الأخرى تمامًا كما تظهر في المستند الأصلي، دون تلخيص أو إعادة صياغة، خاصةً في حقل 'سبب الاشتباه'. "
    "قم بتصحيح أي انعكاس أو تشويش في النص العربي قبل الاستخلاص. استخدم القيمة 'غير متوفر' للحقول غير الموجودة. "
    
    # 💡 التوجيهات الجديدة لاستخلاص الدلالة
    "بعد استخلاص البيانات، قم بتحليل نص حقل 'سبب الاشتباه' واختر رقم الدلالة الأنسب من القائمة أدناه: "
    "1: تكرار العمليات المالية (إيداعات، حوالات سحوبات مشتريات) في حساب المقيم لا تتناسب مع دخله السنوي. "
    "2: تحويلات أو إيداعات نقدية من حساب عميل مقيم الى حساب فرد سعودي أو كيان تجاري. "
    "3: حوالات صادرة أو عمليات مالية متنوعة من حساب مقيم أجنبي لعمليات سداد مصروفات تنم عن المتاجرة فيها أو إعادة بيعها. "
    "4: حوالات دولية صادرة من حساب فرد سعودي أو حساب كيان تجاري إلى حسابات أشخاص بشكل متكرر لا تربطهم به غرض أو علاقة عمل. "
    "5: مقيم يقوم بتنفيذ عمليات تحويل مالية خارج المملكة له أو لأشخاص آخرين بمبالغ لا تتناسب مع دخله وقد يكون مصدرها إيداعات نقدية من عدة عملاء مقيمين. "
    "6: حوالات دولية واردة للحساب الشخصي للمقيم أو للبطاقات الائتمانية بمبالغ عالية تنم عن إدارة نشاط تجاري داخل المملكة. "
    "7: شخص مقيم يقوم بتنفيذ عمليات مالية (إيداع شيك أو صرف شيك أو استقبال حواله مالية) وليس لديه حساب بنكي (عميل عابر). "
    "8: إيداعات نقدية في حساب كيان تجاري بشكل متكرر أو إيداعات مبيعات نقاط بيع، يليها تنفيذ حوالات خارجية أو داخلية لعدة عملاء مقيمين أو عمليات سحب. "
    "9: حوالات دولية واردة أو صادرة لحساب الكيان التجاري لا تتناسب مع نشاط الكيان التجاري. "
    "10: تفويض أجنبي على حساب بنكي عائد لكيان تجاري وتمكينه من الحساب بشكل كامل دون وجود مبرر أو غرض واضح. "
    "11: فتح عدة حسابات الفروع كيان تجاري لنفس النشاط دون وجود ارتباط واضح بين هذه الحسابات، نظراً لإدارة الحساب الخاص بالفرع من قبل المقيم. "
    "يجب أن تكون القيمة المستخلصة في حقل 'رقم الدلالة' هي **الرقم فقط** (مثل: 1 أو 8 أو غير متوفر)."
)


# ===============================
# 3. الدوال المساعدة والمعالجة الأولية
# ===============================

def arabic_to_english_numbers(text):
    if not isinstance(text, str):
        return text
    arabic_map = {'٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
                  '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'}
    return text.translate(str.maketrans(arabic_map))


def pre_process_data_fix_dates(data):
    """تبحث عن التواريخ المتلاصقة (مثل 2022/10/052023/10/05) وتقوم بفصلها."""
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
    """يضيف علامة 'مؤشر التشتت' (🔴) للبيانات المشكوك فيها."""
    suspicion_indicator = ""
    
    # --- 1. التحقق من التواريخ الهجرية ---
    date_fields = ["تاريخ الصادر", "تاريخ الوارد"]
    for field in date_fields:
        date_val = data.get(field, "")
        try:
            date_str_en = arabic_to_english_numbers(str(date_val))
            parts = re.split(r'[/\-.]', date_str_en)
            if len(parts) == 3:
                year_str = re.sub(r'[^\d]', '', parts[0])
                year = int(year_str) if year_str else 0
                if year > 100 and year < 1400: 
                    suspicion_indicator += f"🔴 ({field}: سنة غير طبيعية) "
        except Exception:
            if str(date_val).strip() not in ['غير متوفر', '']:
                 suspicion_indicator += f"🔴 ({field}: صيغة غير مفهومة) "
            pass
    
    # --- 2. التحقق من القيم المالية المستخلصة كصفر ---
    financial_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    for field in financial_fields:
        val = data.get(field, "")
        if str(val).strip() in ['0', '0.00', '٠', '٠,٠٠']:
             suspicion_indicator += f"⚠️ ({field} = 0) "
             
    return suspicion_indicator.strip() or "✅ سليم"

# ===============================
# 2. وظائف المعالجة (بمحاولة واحدة)
# ===============================
def extract_financial_data(file_bytes, file_name, file_type):
    """يستخلص البيانات بمحاولة واحدة فقط."""
    if not GEMINI_API_KEY:
        return None
        
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        mime_type = "application/pdf" if file_type=='pdf' else f"image/{'jpeg' if file_type=='jpg' else file_type}"
        content_parts = [
            "قم باستخلاص جميع البيانات...",
            {"inlineData": {"data": base64.b64encode(file_bytes).decode('utf-8'), "mimeType": mime_type}}
        ]
        config = {
            "systemInstruction": SYSTEM_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA
        }
        
        # تم إزالة حلقة التكرار
        response = client.models.generate_content(model=MODEL_NAME, contents=content_parts, config=config)
            
        extracted_data = json.loads(response.text)
        
        extracted_data = pre_process_data_fix_dates(extracted_data) 
        
        extracted_data['اسم الملف'] = file_name
        
        riyadh_tz = pytz.timezone('Asia/Riyadh')
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now(tz=riyadh_tz).strftime("%Y-%m-%d %H:%M:%S")
        extracted_data['مؤشر التشتت'] = check_for_suspicion(extracted_data) 
        
        return extracted_data 

    except APIError as e:
        st.error(f"❌ فشلت محاولة الاستخلاص من '{file_name}': {e}")
        return None 
    
    except Exception as e:
        st.error(f"❌ خطأ غير متوقع أثناء الاستخلاص من '{file_name}': {e}")
        return None
    
# ===============================
# 3. وظائف التقرير وقاعدة البيانات
# ===============================

def create_final_report_from_db(records, column_names):
    import xlsxwriter
    if not records: 
        st.warning("لا توجد بيانات في قاعدة البيانات لتصديرها.")
        return None
        
    df = pd.DataFrame(records, columns=column_names)

    # 💡 إضافة نص الدلالة الكامل للمراجعة في التقرير النهائي
    if 'رقم الدلالة' in df.columns:
        def get_delala_description(num):
            try:
                num_int = int(str(num).strip())
                return DELALAT_MAPPING.get(num_int, f"رقم الدلالة {num} غير معروف")
            except:
                return "غير محدد"
                
        # يتم إدخال العمود الجديد بناءً على رقم الدلالة
        df.insert(df.columns.get_loc('رقم الدلالة') + 1, 'نص الدلالة المطابقة', df['رقم الدلالة'].apply(get_delala_description))

    # إضافة عمود التسلسل
    df.insert(0, '#', range(1, len(df) + 1))
    
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    
    sheet_name = 'التقرير المالي النهائي' 
    
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    
    workbook, worksheet = writer.book, writer.sheets[sheet_name]
    worksheet.right_to_left()
    col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
    
    for i, col_name in enumerate(df.columns):
        if col_name in ['سبب الاشتباه', 'نص الدلالة المطابقة']:
            worksheet.set_column(i, i, 120, col_format)
        else:
            width = 25 if col_name in ["اسم المشتبه به", "رقم صاحب العمل/ السجل التجاري", "اسم الملف", "وقت الاستخلاص"] else 18
            worksheet.set_column(i, i, width, col_format)
            
    writer.close()
    output.seek(0)
    return output.read()

def display_basic_stats():
    """يعرض عدد السجلات المحفوظة في قاعدة البيانات."""
    st.markdown("---")
    st.subheader("إحصائيات عامة 📈")
    
    report_data = fetch_all_reports() 
    
    total_count = 0
    if report_data and report_data[0]:
        records, _ = report_data
        total_count = len(records)
    
    st.metric(
        label="إجمالي عدد السجلات/الملفات المحفوظة", 
        value=total_count,
        help="يمثل عدد جميع التقارير التي تم تأكيد حفظها في قاعدة البيانات."
    )
    st.markdown("---")

# ===============================
# 4. تنسيق الواجهة (CSS)
# ===============================
st.markdown(
    """
    <style>
    /* خلفية عامة */
    .stApp {
        background-color: #f5f7fa;
        font-family: "Tajawal", sans-serif;
    }
    /* العناوين */
    h1, h2, h3 {
        color: #1a3c6e !important;
        font-weight: 700 !important;
    }
    /* تنسيق الخط */
    p, div, span {
        font-size: 16px !important;
    }
    /* الأزرار */
    .stButton button {
        background-color: #1a3c6e !important;
        color: white !important;
        border-radius: 10px !important;
        padding: 10px 25px !important;
        font-size: 17px !important;
        transition: 0.3s;
    }
    .stButton button:hover {
        background-color: #102649 !important;
        transform: scale(1.05);
    }
    /* الجدول */
    .stDataFrame table {
        border-radius: 10px !important;
    }
    .dataframe tbody tr:nth-child(odd) {
        background-color: #eef2f7 !important;
    }
    .dataframe tbody tr:hover {
        background-color: #d7e3ff !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# ===============================
# 5. واجهة المستخدم الرئيسية (تم تصحيح المسافات البادئة هنا)
# ===============================
def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")

    st.title("📄 نظام استخلاص البيانات بالذكاء الاصطناعي")
    st.markdown("---")

    # تهيئة Session State 
    if 'extracted_data_df' not in st.session_state:
        st.session_state['extracted_data_df'] = pd.DataFrame()

    uploaded_files = st.file_uploader(
        "📤 قم بتحميل الملفات (يمكنك اختيار عدة ملفات)",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        all_extracted_data = []
        
        if st.button("🚀 بدء الاستخلاص"):
            
            extraction_tasks = []
            for uploaded_file in uploaded_files:
                file_bytes, file_name = uploaded_file.read(), uploaded_file.name
                file_type = file_name.split('.')[-1].lower()
                extraction_tasks.append((file_bytes, file_name, file_type))

            st.info(f"⏳ جاري معالجة {len(extraction_tasks)} ملفات بالتوازي... قد يستغرق هذا بعض الوقت.")

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = [executor.submit(extract_financial_data, bytes, name, type) 
                           for bytes, name, type in extraction_tasks]
                
                progress_bar = st.progress(0)
                processed_count = 0

                for future in concurrent.futures.as_completed(results):
                    data = future.result()
                    if data:
                        all_extracted_data.append(data)
                    
                    processed_count += 1
                    progress_bar.progress(processed_count / len(extraction_tasks))
            
            if all_extracted_data:
                st.success("✅ اكتمل الاستخلاص المتوازي لجميع الملفات.")
                new_df = pd.DataFrame(all_extracted_data)
                
                display_cols = ["مؤشر التشتت", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
                new_df = new_df.reindex(columns=display_cols, fill_value='غير متوفر')
                
                st.session_state['extracted_data_df'] = pd.concat(
                    [st.session_state['extracted_data_df'], new_df], 
                    ignore_index=True
                )
            else:
                st.error("❌ فشل استخلاص أي بيانات. يرجى مراجعة الأخطاء أعلاه.")


    # ======================================================
    # 📋 جدول البيانات بعد الاستخلاص + قابل للتعديل
    # ======================================================
    # 💡 تم تصحيح المسافة البادئة لهذه الكتلة لتكون داخل دالة main()
    if not st.session_state['extracted_data_df'].empty:
        st.subheader("✏️ جميع البيانات المستخلصة (قابلة للتعديل)")

        # 💡 زر استخراج الدلالة
        if st.button("💡 استخرج نص الدلالة المطابقة"):
            temp_df = st.session_state['extracted_data_df'].copy()
            
            # نحذف العمود المؤقت قبل الإضافة لضمان عدم التكرار إذا تم الضغط أكثر من مرة
            if 'نص الدلالة المطابقة (للمراجعة)' in temp_df.columns:
                 temp_df.drop(columns=['نص الدلالة المطابقة (للمراجعة)'], inplace=True, errors='ignore')
            
            def get_delala_description(row):
                delala_num = str(row.get('رقم الدلالة', 'غير متوفر')).strip()
                try:
                    num = int(delala_num)
                    return f"({num}) {DELALAT_MAPPING.get(num, 'رقم الدلالة المستخلصة غير صحيح')}"
                except ValueError:
                    return delala_num
            
            if 'رقم الدلالة' in temp_df.columns:
                temp_df.insert(
                    temp_df.columns.get_loc('رقم الدلالة') + 1,
                    'نص الدلالة المطابقة (للمراجعة)',
                    temp_df.apply(get_delala_description, axis=1)
                )
            
            st.session_state['extracted_data_df'] = temp_df
            st.rerun()
            
        # عرض الجدول القابل للتعديل
        edited_df = st.data_editor(
            st.session_state['extracted_data_df'],
            use_container_width=True,
            num_rows="dynamic"
        )

        st.markdown("---")

        # زر الحفظ
        if st.button("💾 تأكيد وحفظ التعديلات في قاعدة البيانات"):
            saved_count = 0
            total_rows = len(edited_df)
            status_placeholder = st.empty() 

            for index, row in edited_df.iterrows():
                row_data = dict(row)
                
                # حذف الأعمدة المؤقتة قبل الإرسال لقاعدة البيانات
                if 'مؤشر التشتت' in row_data:
                    del row_data['مؤشر التشتت']
                if 'نص الدلالة المطابقة (للمراجعة)' in row_data:
                    del row_data['نص الدلالة المطابقة (للمراجعة)']
                    
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
            elif saved_count == 0 and total_rows > 0:
                 status_placeholder.error("❌ فشل حفظ جميع السجلات. يرجى مراجعة رسائل الخطأ الحمراء أعلاه.")


    # ----------------------------------------------------
    # قسم الإحصائيات العامة (المعدل)
    # ----------------------------------------------------
    display_basic_stats()
    
    # ----------------------------------------------------
    # قسم التصدير من قاعدة البيانات
    # ----------------------------------------------------
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
2. ملف db.py (تم تضمين رقم الدلالة)
Python

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

# محاولة استيراد مكتبة التحويل الهجري
try:
    from hijri_converter import Hijri
except ImportError:
    # لا نوقف التنفيذ، فقط نترك تحذير
    Hijri = None

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

# قائمة الأعمدة النهائية في قاعدة البيانات (تم إضافة "رقم الدلالة")
DB_COLUMN_NAMES = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي إيداع الدراسة",
    "رقم الدلالة", # 💡 العمود الجديد
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
            # st.error("❌ متغير DATABASE_URL غير موجود. يرجى مراجعة ملف .env")
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

    # معالجة الأخطاء الشائعة في قراءة السنة الهجرية 
    if y < 1000 and y >= 400:
        y += 1000 
    elif y >= 1 and y <= 99:
        if y < 60: 
            y += 1400
        else:
            y += 1300
    
    # تحقق من نطاق السنة الهجرية المعقول
    if y > 1300 and y < 1500:
        if 1 <= m <= 12 and 1 <= d <= 30:
            try:
                gregorian_date = Hijri(y, m, d).to_gregorian()
                return gregorian_date 
            except Exception:
                return None
                
    return None

def clean_data_type(key, value):
    """تنظيف وتحويل القيم إلى تنسيقات صالحة لـ PostgreSQL."""
    
    # 1. التعامل مع القيم الفارغة
    if value is None or value == 'غير متوفر' or value == '' or pd.isna(value):
        return None

    # 2. تحويل الأعمدة الرقمية (NUMERIC/INTEGER)
    numeric_fields = ["رصيد الحساب", "الدخل السنوي", "إجمالي إيداع الدراسة"]
    
    # 💡 يتم تطبيق تحويل الرقم على 'رقم الدلالة'
    if key in numeric_fields or key == "رقم الدلالة":
        try:
            cleaned_value = arabic_to_english_numbers(str(value))
            
            # منطق رقم الدلالة (يجب أن يكون INTEGER)
            if key == "رقم الدلالة":
                # ننظف من أي أحرف غير رقمية
                num_str = re.sub(r'[^\d]', '', cleaned_value)
                if not num_str:
                    return None
                num = int(num_str)
                # حفظ قيمة NULL إذا كانت خارج النطاق (1-11)
                return num if 1 <= num <= 11 else None 
            
            # منطق الأرقام المالية (المتغير)
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
        
        is_hijri_expected = key in ["تاريخ الصادر", "تاريخ الوارد", "تاريخ الدارسة من", "تاريخ الدراسة الى"]

        # أ. محاولة تحويل ميلادي مباشر
        if not is_hijri_expected:
            try:
                date_obj = pd.to_datetime(clean_str_base, errors='coerce', dayfirst=False)
                if pd.notna(date_obj) and date_obj.year > 1800:
                    return date_obj.date()
            except Exception:
                pass
        
        # ب. محاولة التحويل الهجري 
        if Hijri:
            try:
                parts = [p for p in re.split(r'[/\-.]', clean_str_base) if p.strip()] 
                
                if len(parts) == 3:
                    possible_orders = set(permutations(parts))

                    for p in possible_orders:
                        result = _convert_hijri_to_date(p)
                        if result:
                            return result
                            
            except Exception as e:
                #st.error(f"❌ خطأ داخلي في تحويل التاريخ الهجري لـ '{key}'. القيمة المنظفة: '{clean_str_base}'. الخطأ: {e}")
                pass 
        
        if clean_str_base and key in date_fields:
            # st.warning(f"❌ فشل تحويل التاريخ لـ '{key}'. القيمة الخام: '{value}'. سيتم حفظ NULL.")
            pass
            
        return None

    # 4. القيم الأخرى (VARCHAR/TEXT)
    return value


def save_to_db(extracted_data):
    """يحفظ البيانات المستخلصة إلى جدول تقارير_الاشتباه."""
    conn = connect_db()
    if not conn:
        return False
        
    processed_data_for_display = {}
    insert_columns = []
    insert_values = []
    
    for key in DATA_KEYS:
        # التأكد من أن المفتاح موجود في extracted_data
        value = extracted_data.get(key)
        
        processed_value = clean_data_type(key, value)
        
        processed_data_for_display[key] = str(processed_value) if isinstance(processed_value, datetime.date) else processed_value

        insert_columns.append(sql.Identifier(key))
        insert_values.append(sql.Literal(processed_value))

    st.info("✅ هذه هي البيانات النهائية التي سيتم حفظها في قاعدة البيانات:")
    st.json(processed_data_for_display)

    
    try:
        cur = conn.cursor()
        
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
        # st.success("✅ تم حفظ السجل بنجاح في قاعدة البيانات!") # يتم عرضها في app.py
        return True
    except Exception as e:
        # st.error(f"❌ حدث خطأ أثناء حفظ البيانات: {e}") # يتم عرضها في app.py
        if 'does not exist' in str(e):
             st.error("💡 ملاحظة: إذا ظهر هذا الخطأ، فتأكد أنك أنشأت عمود 'رقم الدلالة' في جدول PostgreSQL الخاص بك بنوع **INTEGER**.")
        
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
        
        # التأكد من جلب جميع الأعمدة المحددة لكي يتطابق مع DataFrame في app.py
        select_columns = sql.SQL(', ').join([sql.Identifier(col) for col in DB_COLUMN_NAMES])

        select_query = sql.SQL('SELECT id, {columns} FROM public.تقارير_الاشتباه').format(columns=select_columns)
        
        cur.execute(select_query)
        
        # يجب دمج عمود id مع أسماء الأعمدة الأخرى
        column_names = ['id'] + [desc[0] for desc in cur.description[1:]] 
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return records, column_names

    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء جلب البيانات من قاعدة البيانات: {e}")
        if conn:
            conn.close()
        return None, None
