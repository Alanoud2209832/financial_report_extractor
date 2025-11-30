import streamlit as st
import pandas as pd
import json
import io
import time
import base64
import os
# تأكدنا من عدم استخدام SQLite أو Session State
import fitz # PyMuPDF library for PDF processing
from PIL import Image # Pillow library for image handling
from google import genai
from google.genai.errors import APIError

# ----------------------------------------------------------------
# 1. إعدادات API والثوابت والحقول المطلوبة
# ----------------------------------------------------------------

# 🚨 هام: يجب تعيين مفتاح API الخاص بكِ هنا!
# يرجى استبدال النص الفارغ التالي بمفتاح Gemini API الصالح
GEMINI_API_KEY = "AIzaSyBVJvH_Z5AX9dwXR7UFhbeo9iB5-aL-rZI" # ⬅️ يرجى لصق المفتاح الصالح هنا بين علامات التنصيص

# تهيئة موديل Gemini (نستخدم flash للسرعة والأداء الممتاز في الاستخلاص)
MODEL_NAME = 'gemini-2.5-flash-preview-09-2025'
SYSTEM_PROMPT = (
    "أنت خبير في تحليل التقارير المالية. مهمتك هي قراءة النص والصورة المستخرجة من وثيقة "
    "مالية وتحويله إلى كائن JSON وفقًا للمخطط المحدد بدقة. يجب أن تكون دقيقًا جدًا في "
    "استخلاص القيم. قم بتصحيح أي انعكاس أو تشويش في النص العربي قبل الاستخلاص. "
    "استخدم القيمة 'غير متوفر' للحقول غير الموجودة."
)

# أسماء الحقول المطلوبة باللغة العربية (كما طلبتِ أن تكون في JSON و Excel)
REPORT_FIELDS_ARABIC = [
    "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
    "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
    "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
    "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
    "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
    "إجمالي الإيداع على الحساب اثناء الدراسة"
]

# مخطط الاستجابة لـ Gemini (JSON Schema) - يستخدم الحقول العربية مباشرة
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
# 2. وظائف معالجة الملفات والاستخلاص (لا يوجد تخزين دائم)
# ----------------------------------------------------------------

def convert_pdf_to_images(file_bytes):
    """تحويل ملف PDF إلى قائمة من صور PNG (باستخدام الصفحة الأولى فقط)."""
    try:
        # Check if fitz (PyMuPDF) is available
        if 'fitz' not in globals():
             st.error("خطأ: مكتبة PyMuPDF (fitz) غير مثبتة. الرجاء تثبيتها باستخدام الأمر: pip3 install PyMuPDF")
             return []
             
        pdf_document = fitz.open(stream=file_bytes, filetype="pdf")
        page = pdf_document.load_page(0)
        matrix = fitz.Matrix(3.0, 3.0) # دقة عالية لـ OCR أفضل
        pix = page.get_pixmap(matrix=matrix)
        img_bytes = pix.tobytes(output='png')
        return [img_bytes]
    except Exception as e:
        st.error(f"حدث خطأ أثناء تحويل PDF إلى صورة: {e}. يرجى التأكد من تثبيت PyMuPDF.")
        return []

def extract_financial_data(file_bytes, file_name, file_type):
    """
    يتلقى بيانات الملف ويستخدم Gemini API لاستخلاص البيانات المالية
    وإرجاعها مباشرة كـ JSON.
    """
    if not GEMINI_API_KEY:
        st.error("🚨 الرجاء تحديث 'GEMINI_API_KEY' في الكود بمفتاح صالح قبل تحميل الملف.")
        return None
        
    response = None
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # 1. تحديد المحتوى المتعدد الوسائط
        content_parts = [
            "قم باستخلاص جميع البيانات من هذه الوثيقة المالية "
            "وحوّلها إلى كائن JSON يطابق المخطط المحدد بدقة. "
            "يرجى استخدام الحقول العربية المطلوبة كمفاتيح JSON. "
            "إذا لم تتمكن من العثور على قيمة حقل معين، ضع القيمة: 'غير متوفر'."
        ]
        
        if file_type == 'pdf':
            st.info("تم الكشف عن ملف PDF. جاري تحويل الصفحة الأولى إلى صورة...")
            image_bytes_list = convert_pdf_to_images(file_bytes)
            
            if not image_bytes_list:
                return None
                
            for img_bytes in image_bytes_list:
                content_parts.append({
                    "inlineData": {
                        "data": base64.b64encode(img_bytes).decode('utf-8'),
                        "mimeType": "image/png"
                    }
                })
        
        elif file_type in ['png', 'jpg', 'jpeg']:
            content_parts.append({
                "inlineData": {
                    "data": base64.b64encode(file_bytes).decode('utf-8'),
                    "mimeType": f"image/{file_type}" 
                }
            })
        else:
            st.error(f"نوع الملف غير مدعوم: {file_type}")
            return None

        # 2. إعدادات التوليد
        config = {
            "systemInstruction": SYSTEM_PROMPT,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        }

        # 3. طلب توليد المحتوى
        st.info(f"⏳ جاري استخلاص البيانات من '{file_name}'...")
        
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=content_parts,
            config=config,
        )

        # 4. معالجة الاستجابة
        json_output = response.text
        extracted_data = json.loads(json_output)
        
        # إضافة اسم الملف ووقت الاستخلاص (للمرجع في الجدول النهائي)
        extracted_data['اسم الملف'] = file_name
        extracted_data['وقت الاستخلاص'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

        st.success(f"✅ تم استخلاص البيانات من التقرير: '{file_name}' بنجاح!")
        return extracted_data

    except APIError as e:
        st.error(f"🚨 خطأ في الاتصال بـ Gemini API. تأكدي من صحة المفتاح. الخطأ: {e}")
    except json.JSONDecodeError:
        st.error(f"❌ فشل في تفسير استجابة النموذج كـ JSON. يرجى مراجعة الاستجابة.")
    except Exception as e:
        st.error(f"❌ حدث خطأ غير متوقع: {e}")
    return None

def create_final_report(extracted_data):
    """تحويل البيانات المستخلصة إلى ملف Excel (XLSX) بتنسيق RTL."""
    if not extracted_data:
        return None
        
    # تحديد ترتيب الأعمدة في ملف Excel النهائي
    column_order = ["#", "اسم الملف", "وقت الاستخلاص"] + REPORT_FIELDS_ARABIC
    
    # تحويل البيانات إلى إطار بيانات (DataFrame)
    df = pd.DataFrame([extracted_data])
    df.insert(0, '#', 1)
    
    # إعادة ترتيب الأعمدة وإضافة الأعمدة الناقصة (لضمان وجود الـ 20 حقل)
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
        # استخدام xlsxwriter لإنشاء ملف Excel ودعم RTL والتنسيق
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        df.to_excel(writer, sheet_name='التقرير المالي', index=False)
        
        workbook  = writer.book
        worksheet = writer.sheets['التقرير المالي']
        worksheet.right_to_left()

        # تنسيق العمود الخاص بـ "سبب الاشتباه" لضمان ظهور النص كاملاً
        col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
        # نفترض أن عمود "سبب الاشتباه" هو العمود رقم 17 (حسب الترتيب المحدد)
        worksheet.set_column('R:R', 60, col_format) 
        
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

    st.title("📄 أداة استخلاص التقارير المالية الآلية (للعرض الفوري)")
    st.caption("هذا التطبيق يستخلص البيانات من الملف المحمل مباشرة ويحولها إلى Excel دون تخزين.")
    st.markdown("---")

    # قسم تحميل الملف
    uploaded_file = st.file_uploader(
        "📂 قم بتحميل ملف التقرير المالي (PDF أو صورة) هنا:",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=False
    )

    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        file_name = uploaded_file.name
        file_type = file_name.split('.')[-1].lower()
        
        st.success(f"تم تحميل ملف: **{file_name}**")
        
        # زر التشغيل لفصل عملية التحميل عن عملية الاستخلاص الطويلة
        if st.button("🚀 بدء الاستخلاص والتحويل إلى Excel", key="start_extraction"):
            with st.spinner("⏳ جاري تحليل الوثيقة واستخلاص البيانات وتجهيز ملف Excel..."):
                
                extracted_data = extract_financial_data(file_bytes, file_name, file_type)
                
                if extracted_data:
                    st.subheader("✅ البيانات المستخلصة (جاهزة للتنزيل)")
                    
                    # عرض البيانات المستخلصة كجدول (للتأكد)
                    df_display = pd.DataFrame([extracted_data])
                    # حذف اسم الملف ووقت الاستخلاص من العرض السريع (اختياري)
                    if 'اسم الملف' in df_display.columns: del df_display['اسم الملف']
                    if 'وقت الاستخلاص' in df_display.columns: del df_display['وقت الاستخلاص']
                    st.dataframe(df_display, use_container_width=True, height=200)

                    excel_data_bytes = create_final_report(extracted_data)
                    
                    if excel_data_bytes:
                        st.subheader("🎉 ملف Excel جاهز للتحميل")
                        st.balloons()
                        
                        st.download_button(
                            label="⬇️ تحميل ملف التقرير النهائي (Excel XLSX)",
                            data=excel_data_bytes,
                            file_name=f"{file_name.replace('.pdf', '').replace(f'.{file_type}', '')}_Extracted_Report.xlsx",
                            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                        )
                    else:
                        st.error("❌ فشل في إنشاء ملف Excel. الرجاء مراجعة سجل الأخطاء.")
                else:
                    st.warning("لم يتم استخلاص أي بيانات. يرجى مراجعة رسائل الخطأ في الأعلى.")
    
if __name__ == '__main__':
    main()
