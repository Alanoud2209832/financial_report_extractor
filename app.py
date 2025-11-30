import streamlit as st
import pandas as pd
import json
import io
import base64
from google import genai
from google.genai.errors import APIError

# ----------------------------------------------------------------
# 1. إعدادات API والثوابت
# ----------------------------------------------------------------

# 🚨 هام: يجب تعيين مفتاح API الخاص بكِ هنا!
# يرجى استبدال النص الفارغ التالي بمفتاح Gemini API الصالح
GEMINI_API_KEY = "AIzaSyCeNFMTQjPhKMk0hN5qA_Lk-256RpExmN0" # ⬅️ الرجاء لصق المفتاح الصالح هنا بين علامتي التنصيص

# تهيئة موديل Gemini
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    # 🌟 التعديل هنا: التركيز على الاستخلاص الحرفي والنسخ الدقيق للبيانات، خاصة في الحقول النصية الطويلة.
    "أنت نظام استخلاص بيانات آلي (OCR/NLP). مهمتك هي قراءة النص والصورة المستخرجة من الوثيقة المالية "
    "وتحويل البيانات إلى كائن JSON وفقاً للمخطط المحدد بدقة. يجب عليك **نسخ** جميع القيم المستخلصة "
    "تماماً كما تظهر في المستند الأصلي، دون تلخيص أو إعادة صياغة، خاصةً في حقل 'سبب الاشتباه'. "
    "قم بتصحيح أي انعكاس أو تشويش في النص العربي قبل الاستخلاص. استخدم القيمة 'غير متوفر' للحقول غير الموجودة."
)

# أسماء الحقول المطلوبة باللغة العربية
REPORT_FIELDS_ARABIC = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي الإيداع على الحساب اثناء الدراسة"
]

# مخطط الاستجابة لـ Gemini (JSON Schema)
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        field: {
            "type": "STRING", 
            "description": f"القيمة المستخلصة لـ: {field}"
        } for field in REPORT_FIELDS_ARABIC
    },
    "propertyOrdering": REPORT_FIELDS_ARABIC
}

# ----------------------------------------------------------------
# 2. وظائف المعالجة
# ----------------------------------------------------------------

def extract_financial_data(file_bytes, file_name, file_type):
    """
    تستخدم Gemini API لاستخلاص البيانات المالية مباشرة من بيانات الملف 
    دون الحاجة لرفع الملف إلى File API أو استخدام مكتبة PyMuPDF (fitz).
    """
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # 1. تحديد نوع المحتوى
        if file_type == 'pdf':
            mime_type = "application/pdf"
            # تنبيه حول PDF يتم إرساله مباشرة دون معالجة مسبقة
            st.warning("⚠️ جاري إرسال ملف PDF مباشرةً. قد يستغرق التحليل وقتاً أو يفشل في ملفات PDF المعقدة.")
        elif file_type in ['png', 'jpg', 'jpeg']:
            mime_type = f"image/{'jpeg' if file_type == 'jpg' else file_type}"
        else:
            st.error(f"نوع الملف غير مدعوم: {file_type}")
            return None

        # 2. بناء محتوى الـ Inline Data (الملف نفسه كبيانات مشفرة)
        content_parts = [
            "قم باستخلاص جميع البيانات من هذه الوثيقة المالية "
            "وحوّلها إلى كائن JSON يطابق المخطط المحدد بدقة. "
            "يرجى استخدام الحقول العربية المطلوبة كمفاتيح JSON. "
            "إذا لم تتمكن من العثور على قيمة حقل معين، ضع القيمة: 'غير متوفر'.",
            {
                "inlineData": {
                    "data": base64.b64encode(file_bytes).decode('utf-8'),
                    "mimeType": mime_type
                }
            }
        ]

        # 3. إعدادات التوليد
        config = {
            "systemInstruction": SYSTEM_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        }

        with st.spinner(f"⏳ جاري استخلاص البيانات من '{file_name}'..."):
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=content_parts,
                config=config,
            )

        json_output = response.text
        extracted_data = json.loads(json_output)
        
        extracted_data['اسم الملف'] = file_name
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

        st.success(f"✅ تم استخلاص البيانات من التقرير: '{file_name}' بنجاح!")
        return extracted_data

    except APIError as e:
        st.error(f"🚨 خطأ في الاتصال بـ Gemini API. تأكد من صحة المفتاح. الخطأ: {e}")
    except json.JSONDecodeError:
        st.error(f"❌ فشل في تفسير استجابة النموذج كـ JSON. يرجى مراجعة الاستجابة.")
    except Exception as e:
        st.error(f"❌ حدث خطأ غير متوقع: {e}")
    return None

def create_final_report(extracted_data):
    """تحويل البيانات المستخلصة إلى ملف Excel (XLSX) بتنسيق RTL."""
    import xlsxwriter
    
    if not extracted_data:
        return None
        
    column_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
    
    df = pd.DataFrame([extracted_data])
    df.insert(0, '#', 1)
    
    final_cols = []
    for col in column_order:
        if col in df.columns: 
            final_cols.append(col)
        elif col not in df.columns:
            df[col] = 'غير متوفر'
            final_cols.append(col)
            
    df = df[final_cols]
    
    output = io.BytesIO()
    
    try:
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        df.to_excel(writer, sheet_name='التقرير المالي', index=False)
        
        workbook  = writer.book
        worksheet = writer.sheets['التقرير المالي']
        worksheet.right_to_left()

        col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
        
        # العمود 'سبب الاشتباه' هو العمود رقم 21 (يُرمز له بـ U في Excel)
        # تم تعديل العرض إلى 120 لضمان احتواء النصوص الطويلة مع تفعيل الالتفاف التلقائي
        worksheet.set_column('U:U', 120, col_format) 
        
        # تطبيق التنسيق العام على باقي الأعمدة لضمان القراءة
        for i, col_name in enumerate(final_cols):
             # تجنب إعادة ضبط عمود "سبب الاشتباه"
            if col_name != 'سبب الاشتباه':
                # ضبط عرض افتراضي مناسب لباقي الحقول
                width = 25 if col_name in ["اسم المشتبه به", "رقم صاحب العمل/ السجل التجاري"] else 18
                worksheet.set_column(i, i, width, col_format)

        writer.close()
        output.seek(0)
        
        return output.read()
        
    except Exception as e:
        st.error(f"🚨 حدث خطأ أثناء إنشاء ملف Excel: {e}")
        return None


# ----------------------------------------------------------------
# 3. واجهة المستخدم (Streamlit UI)
# ----------------------------------------------------------------

def main():
    st.set_page_config(layout="wide", page_title="أداة استخلاص وتقارير مالية")

    st.markdown("""
    <style>
        .stApp { background-color: #f0f2f6; }
        .stButton>button {
            background-color: #1a73e8; /* Google Blue */
            color: white; 
            border-radius: 8px;
            padding: 10px 20px;
            font-size: 16px;
            font-weight: bold;
            transition: background-color 0.3s;
        }
        .stButton>button:hover { background-color: #1558b5; }
    </style>
    """, unsafe_allow_html=True)

    
    # ⚠️ فحص المفتاح
    if GEMINI_API_KEY == "" or not GEMINI_API_KEY:
        st.error("`GEMINI_API_KEY` داخل الكود.")
        return # إيقاف التنفيذ

    # قسم تحميل الملف
    uploaded_file = st.file_uploader(
        "قم بتحميل ملف التقرير",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=False
    )

    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        file_name = uploaded_file.name
        file_type = file_name.split('.')[-1].lower()
        
        st.success(f"تم تحميل ملف: **{file_name}**")
        
        if st.button("بدء الاستخلاص والتحويل إلى Excel", key="start_extraction"):
            
            extracted_data = extract_financial_data(file_bytes, file_name, file_type)
            
            if extracted_data:
                st.subheader("✅ البيانات المستخلصة (جاهزة للتنزيل)")
                
                df_display = pd.DataFrame([extracted_data])
                # إزالة أعمدة الميتا داتا من العرض الجدولي (لكن تبقى في Excel)
                if 'اسم الملف' in df_display.columns: del df_display['اسم الملف']
                if 'وقت الاستخلاص' in df_display.columns: del df_display['وقت الاستخلاص']
                st.dataframe(df_display, use_container_width=True, height=200)

                excel_data_bytes = create_final_report(extracted_data)
                
                if excel_data_bytes:
                    st.subheader("ملف Excel جاهز للتحميل")
                    st.balloons()
                    
                    st.download_button(
                        label="⬇️ تحميل ملف التقرير النهائي (Excel XLSX)",
                        data=excel_data_bytes,
                        file_name=f"{file_name.replace('.pdf', '').replace(f'.{file_type}', '')}_Extracted_Report.xlsx",
                        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    )
                else:
                    st.error("❌ فشل في إنشاء ملف Excel.")
            else:
                st.warning("لم يتم استخلاص أي بيانات. يرجى مراجعة رسائل الخطأ.")
    
if __name__ == '__main__':
    main()
