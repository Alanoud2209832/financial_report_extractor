import streamlit as st
import pandas as pd
from google import genai
from google.genai import types
from google.genai.errors import APIError
from arabic_reshaper import reshape
from bidi.algorithm import get_display
import os
import json
import io
import time 
from firebase_admin import initialize_app, firestore, credentials, get_app
from google.cloud.exceptions import NotFound

# ----------------------------------------------------------------
# 1. API Setup, Arabic Text Helpers, and Firebase Initialization
# ----------------------------------------------------------------

# 🚨 IMPORTANT: Set your Gemini API key here!
GEMINI_API_KEY = "AIzaSyAwi0kwDln4fKeyWBy4DupPUXTuPYuLeWY" # Please paste a valid key here!

# Initialize Gemini Client
client = None
try:
    if GEMINI_API_KEY:
        client = genai.Client(api_key=GEMINI_API_KEY)
        os.environ['GEMINI_API_KEY'] = GEMINI_API_KEY
    else:
         client = genai.Client()
except Exception as e:
    error_message = f"Failed to initialize Gemini Client: {e}"
    st.error(error_message)

if client is None:
    st.error("❌ Gemini Client initialization failed. Ensure a valid API key is provided.")

# Arabic Text Fixer (Uses Reshaper and BiDi)
def fix_arabic(text):
    """Processes Arabic text for correct Right-to-Left display."""
    if isinstance(text, str) and text:
        reshaped_text = reshape(text)
        return get_display(reshaped_text)
    return text
    
# Helper function for displaying R-T-L Arabic content in Streamlit UI boxes
def rtl_markdown(content, style_type="info"):
    """
    Displays content within an HTML div enforcing RTL direction and Streamlit styling.
    This is used primarily for displaying Arabic data fields and titles.
    """
    
    # Define Streamlit styling (using inline CSS)
    styles = {
        "info": {"bg": "#eff6ff", "border": "#93c5fd", "text": "#1d4ed8"},
        "warning": {"bg": "#fffbeb", "border": "#fcd34d", "text": "#b45309"},
        "success": {"bg": "#ecfdf5", "border": "#6ee7b7", "text": "#059669"},
        "error": {"bg": "#fef2f2", "border": "#fca5a5", "text": "#dc2626"},
    }
    
    style = styles.get(style_type, styles["info"])
    
    # Enforce RTL direction and alignment
    html_template = f"""
    <div style="direction: rtl; text-align: right; 
                background-color: {style['bg']}; 
                border-left: 5px solid {style['border']}; 
                padding: 10px; border-radius: 4px; color: {style['text']}; 
                font-size: 16px; margin-bottom: 10px;">
        {fix_arabic(content)}
    </div>
    """
    st.markdown(html_template, unsafe_allow_html=True)


# -----------------------------------------------------
# 🚀 1.1 Firebase Firestore Initialization for Permanent Storage
# -----------------------------------------------------

# Initialize Firebase using Canvas environment variables
if 'db' not in st.session_state:
    st.session_state.firebase_ready = False
    st.session_state.collection_path = None
    
    try:
        # Read environment variables (available in Canvas)
        FIREBASE_CONFIG_JSON = os.environ.get('__firebase_config', '{}')
        FIREBASE_CONFIG = json.loads(FIREBASE_CONFIG_JSON)
        APP_ID = os.environ.get('__app_id', 'default-app-id')
        
        # Check for essential configuration data
        if FIREBASE_CONFIG and APP_ID and FIREBASE_CONFIG_JSON != '{}':
            
            app_initialized = False
            try:
                get_app() 
                app_initialized = True
            except ValueError:
                pass
                
            if not app_initialized:
                 cred = credentials.Certificate(FIREBASE_CONFIG)
                 initialize_app(cred)
                 
            st.session_state.db = firestore.client()
            st.session_state.collection_path = f"artifacts/{APP_ID}/public/data/financial_reports"
            st.session_state.firebase_ready = True
            
        else:
            # English Warning Message
            st.warning("⚠️ Firebase (Config) setup failed. Permanent storage is DISABLED. Falling back to **Temporary Session Storage** until correct settings are provided.")
    except Exception as e:
        # English Error Message
        st.error(f"❌ Unexpected error during Firebase initialization: {e}. Falling back to **Temporary Session Storage**.")
        
# ----------------------------------------------------------------
# 2. Gemini Multimodal Extraction Function
# ----------------------------------------------------------------

def get_llm_multimodal_output(uploaded_file, client):
    """
    Sends the PDF/Image file as inline data to Gemini to extract the 20 fields into JSON format.
    """
    if client is None:
        st.error("🚨 Cannot communicate with Gemini. Please check if the API key is provided.")
        return None

    try:
        # 1. Read File
        uploaded_file.seek(0)
        file_bytes = uploaded_file.read()
        mime_type = uploaded_file.type 

        if not mime_type or not mime_type.startswith(('application/pdf', 'image/')):
            st.error(f"File format ({mime_type}) is not supported for visual extraction. Please upload a PDF or an image.")
            return None

        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        system_prompt = (
            "You are an exceptional data extraction engine specializing in processing Arabic OCR text "
            "that may be distorted or reversed (Bidi reversal). Your task is to read the attached file, "
            "analyze its visual and textual content accurately, and correct any Arabic text distortions. "
            "The output must be PURE JSON only, strictly adhering to the specified 20 Arabic field keys."
        )

        prompt_text = f"""
        Strictly applying the system instructions, analyze the attached file.
        
        **General Search Directives (The 20 fields required for extraction):**
        1.  **اسم المشتبه به (Suspect Name):** Extract the **full name** (trilateral or quadrilateral) of the suspect as it appears next to 'الوافد /' or 'اسم العميل'.
        2.  **رقم الهوية (ID Number):** Extract the 10-digit resident ID/Iqama number.
        3.  **الجنسية (Nationality):** Extract the nationality as shown in the 'الجنسية' field.
        4.  **تاريخ الميلاد الوافد (Date of Birth):** Extract the suspect's date of birth.
        5.  **تاريخ الدخول (Entry Date):** Extract the date the suspect entered the Kingdom.
        6.  **الحالة الاجتماعية (Marital Status):** Extract the suspect's marital status.
        7.  **المهنة (Profession):** Extract the profession as listed in the document.
        8.  **رقم الجوال (Mobile Number):** Extract the mobile/phone number if found.
        9.  **المدينة (City):** Extract the client's city of residence or the clearest city name.
        10. **رصيد الحساب (Account Balance):** Extract the final account balance.
        11. **الدخل السنوي (Annual Income):** Extract the value of "إجمالي العمليات المضافة لحساب..." as an estimate for annual income.
        12. **رقم الصادر (Outgoing No.):** Extract the 6-digit number appearing after 'رقم الصادر' at the top of the document.
        13. **تاريخ الصادر (Outgoing Date):** Extract the Hijri date next to "التاريخ" associated with "رقم الصادر".
        14. **رقم الوارد (Incoming No.):** Extract the letter number or **Incoming Number** visible in the Ministry of Commerce stamp.
        15. **تاريخ الوارد (Incoming Date):** Extract the date the letter arrived (the date associated with "رقم الوارد").
        16. **رقم صاحب العمل/ السجل التجاري (Employer/Commercial Reg. No.):** Extract the commercial register number of the establishment or the employer's number.
        17. **سبب الاشتباه (Reason for Suspicion):** Extract the **complete and detailed descriptive text paragraph** outlining the reason for suspicion.
        18. **تاريخ الدارسة من (Study Date From):** Extract the start date of the study period.
        19. **تاريخ الدراسة الى (Study Date To):** Extract the end date of the study period.
        20. **إجمالي الإيداع على الحساب اثناء الدراسة (Total Deposit During Study):** Extract the value of "إجمالي العمليات المضافة" or "إجمالي الإيداع على الحساب اثناء الدراسة".
        
        **Note:** If an explicit value is not found for any field, set the value to: 'غير متوفر'.
        
        Please provide the answer in PURE JSON format (without any additional text):
        {{
            "رقم الصادر": "Extracted Value.",
            "تاريخ الصادر": "Extracted Value.",
            "اسم المشتبه به": "Full Extracted Value.",
            "رقم الهوية": "Extracted Value.",
            "الجنسية": "Extracted Value.",
            "تاريخ الميلاد الوافد": "Extracted Value.",
            "تاريخ الدخول": "Extracted Value.",
            "الحالة الاجتماعية": "Extracted Value.",
            "المهنة": "Extracted Value.",
            "رقم الجوال": "Extracted Value.",
            "المدينة": "Extracted Value.",
            "رصيد الحساب": "Extracted Value in SAR.",
            "الدخل السنوي": "Extracted Value in SAR.",
            "رقم الوارد": "Extracted Value.",
            "تاريخ الوارد": "Extracted Value.",
            "رقم صاحب العمل/ السجل التجاري": "Extracted Value.",
            "سبب الاشتباه": "The full descriptive text paragraph.",
            "تاريخ الدارسة من": "Extracted Value.",
            "تاريخ الدراسة الى": "Extracted Value.",
            "إجمالي الإيداع على الحساب اثناء الدراسة": "Extracted Value in SAR."
        }}
        """

        response_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            system_instruction=system_prompt,
            temperature=0.3
        )
        
        # 5. Send Request (File Part + Text Prompt)
        response = client.models.generate_content(
            model='gemini-2.5-pro',
            contents=[file_part, prompt_text],
            config=response_config
        )

        response_text = response.text.replace('\n', '').strip()
        
        # 6. Parse Response
        if response_text.startswith('{') and response_text.endswith('}'):
             extracted_data = json.loads(response_text)
             return extracted_data
        else:
            st.error(f"Failed to extract JSON data. Received unexpected text: {response_text[:100]}...")
            return None

    except APIError as e:
        error_details = str(e)
        if "403 PERMISSION_DENIED" in error_details or "leaked" in error_details:
             st.error("🚨 Error 403 (PERMISSION_DENIED): The Gemini API key you are using is invalid or has been reported as leaked. **Please replace it with a new, valid API key on line 14.**")
        else:
             st.error(f"🚨 Error connecting to Gemini API: {e}")
        return None
    except json.JSONDecodeError:
        st.error("🚨 Error decoding the extracted JSON data. Please try again.")
        return None
    except Exception as e:
        st.error(f"🚨 Unexpected error during extraction: {e}")
        return None


# -----------------------------------------------------
# 3. Data Processing and Storage Functions (Firebase)
# -----------------------------------------------------

@st.cache_data(show_spinner=False)
def get_all_reports_data():
    """Loads all documents from Firestore (if available) or from session state (temporary)."""
    # 1. If Firebase is ready, load from Firestore (Permanent Storage)
    if st.session_state.get('firebase_ready'):
        db_client = st.session_state.get('db')
        collection_path = st.session_state.get('collection_path')
        
        try:
            reports_ref = db_client.collection(collection_path).stream()
            all_reports = []
            for report in reports_ref:
                report_data = report.to_dict()
                report_data['doc_id'] = report.id 
                all_reports.append(report_data)
                
            all_reports.sort(key=lambda x: x.get('#', float('inf')))
            return all_reports
        
        except Exception as e:
            # English Error Message
            st.error(f"❌ Failed to load data from Firestore: {e}. Displaying an empty record temporarily.")
            return []
            
    # 2. If Firebase is not ready, load from temporary session state
    else:
        if 'report_data_temp' not in st.session_state:
            st.session_state.report_data_temp = []
        return st.session_state.report_data_temp


def add_report_to_storage(report_data):
    """Adds a new report to Firestore (if available) or to session state (temporary)."""
    # 1. Attempt to save to Firestore (Permanent Storage)
    if st.session_state.get('firebase_ready'):
        db_client = st.session_state.get('db')
        collection_path = st.session_state.get('collection_path')
        
        data_to_save = report_data.copy()
        if 'doc_id' in data_to_save:
            del data_to_save['doc_id']
            
        try:
            db_client.collection(collection_path).add(data_to_save)
            st.cache_data.clear() # Clear cache to force reload from DB
            st.success("✅ Successfully saved to the Permanent Database (Firebase Firestore).")
            return True
        except Exception as e:
            # English Error Message
            st.error(f"❌ Failed to save data to Firestore: {e}. Data will be saved temporarily to session.")
            # Fallback to temporary session state if permanent save fails
            if 'report_data_temp' not in st.session_state:
                st.session_state.report_data_temp = []
            
            # Recalculate and add serial number (#) for temporary storage
            new_report_data = report_data.copy()
            # Note: We rely on the '#' calculated inside the main loop, so we don't recalculate here unless necessary, but ensure it's added.
            st.session_state.report_data_temp.append(new_report_data)
            return True # Consider operation successful as it's saved in the session

    # 2. Save to temporary session state (when Firebase is not available)
    else:
        if 'report_data_temp' not in st.session_state:
            st.session_state.report_data_temp = []
        
        # Calculate and add serial number (#) based on current temporary reports count
        new_report_data = report_data.copy()
        new_report_data["#"] = len(st.session_state.report_data_temp) + 1
        st.session_state.report_data_temp.append(new_report_data)
        
        # English Warning Message
        st.warning("⚠️ Successfully saved to **Temporary Session Storage**. Data will be LOST upon full page refresh (F5) or browser closure.")
        return True
        
        
def create_final_report(all_reports_data):
    """
    Converts the list of reports into a DataFrame, enforces column order, and generates an Excel file (xlsx).
    """
    if not all_reports_data:
        return None
        
    column_order = [
        "#", "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
        "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
        "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
        "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
        "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
        "إجمالي الإيداع على الحساب اثناء الدراسة"
    ]
    
    df = pd.DataFrame(all_reports_data)
    
    final_cols = []
    for col in column_order:
        if col in df.columns:
            final_cols.append(col)
        else:
            df[col] = ''
            final_cols.append(col)
            
    final_cols_filtered = [col for col in final_cols if col in df.columns and col != 'doc_id']
    df = df[final_cols_filtered]
    
    # Apply BiDi correction to all DataFrame data before exporting to Excel
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(lambda x: get_display(reshape(str(x))) if pd.notna(x) else x)
            
    output = io.BytesIO()
    
    try:
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        sheet_name = fix_arabic('بيانات البلاغات')
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        
        workbook  = writer.book
        worksheet = writer.sheets[sheet_name]
        worksheet.right_to_left()

        if len(final_cols_filtered) > 17:
            col_format = workbook.add_format({'text_wrap': True, 'align': 'right', 'valign': 'top'})
            worksheet.set_column(17, 17, 60, col_format) 
        
        writer.close()
        output.seek(0)
        
        return output.read()
        
    except Exception as e:
        st.error(f"🚨 Error occurred while creating the Excel file: {e}")
        return None

# ----------------------------------------------------------------
# 4. Main Application Interface (Streamlit)
# ----------------------------------------------------------------

def main():
    st.set_page_config(page_title=fix_arabic("أتمتة استخلاص التقارير المالية"), layout="wide")
    
    # English/Arabic Title
    st.markdown(f"<h1 style='text-align: right; direction: rtl;'>{fix_arabic('🤖 Automated Financial Report Extractor (Unified Data Log)')}</h1>", unsafe_allow_html=True)
    st.markdown("---")
    
    # 1. Load all current data (from Firestore or temporary session)
    all_reports_data = get_all_reports_data()
    reports_count = len(all_reports_data)
    
    # 2. Display Storage Status (English)
    if st.session_state.get('firebase_ready'):
        st.info(f"💾 Storage Mode: **Permanent (Firebase Firestore)**. Reports Stored: {reports_count}.")
    else:
        # This message will show when Firebase setup fails
        st.warning(f"⚠️ Storage Mode: **Temporary (Streamlit Session)**. Reports Stored: {reports_count}. Data will be **LOST** upon full page refresh (F5) or browser closure, but retained during minor updates.")

    st.markdown("---") 

    # ------------------------------------------------------------------
    # 3. Display the Unified Static Log
    # ------------------------------------------------------------------
    # English Header
    st.markdown(f"<h3 style='text-align: right; direction: rtl; color: #1e40af;'>{fix_arabic('📊 Current Unified Log (Static Data)')}</h3>", unsafe_allow_html=True)
    
    if all_reports_data:
        # Convert data to DataFrame for display
        df_display = pd.DataFrame(all_reports_data)
        
        column_order_display = [
            "#", "رقم الصادر", "تاريخ الصادر", "اسم المشتبه به", "رقم الهوية",
            "الجنسية", "تاريخ الميلاد الوافد", "تاريخ الدخول", "الحالة الاجتماعية",
            "المهنة", "رقم الجوال", "المدينة", "رصيد الحساب", "الدخل السنوي",
            "رقم الوارد", "تاريخ الوارد", "رقم صاحب العمل/ السجل التجاري",
            "سبب الاشتباه", "تاريخ الدارسة من", "تاريخ الدراسة الى",
            "إجمالي الإيداع على الحساب اثناء الدراسة"
        ]
        
        # Filter and order available columns
        cols_to_display = [col for col in column_order_display if col in df_display.columns and col != 'doc_id']
        
        df_display = df_display[cols_to_display]
        
        # Apply Arabic fixing to cell content (for Streamlit display)
        for col in df_display.columns:
            if df_display[col].dtype == 'object':
                df_display[col] = df_display[col].apply(lambda x: fix_arabic(str(x)) if pd.notna(x) else x)
                
        # Display the table
        st.dataframe(df_display, use_container_width=True, height=300)
        
        # Download button appears below the static table (English Label)
        excel_data_bytes = create_final_report(all_reports_data)
        if excel_data_bytes:
             st.download_button(
                label=fix_arabic("⬇️ Download Unified Report Log (Report_Data.xlsx)"),
                data=excel_data_bytes,
                file_name=fix_arabic("Report_Data.xlsx"),
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
             )

    else:
        # English Message
        st.info("The Unified Log is currently empty. Upload a financial report to start the extraction process.")
    
    st.markdown("---") # Separator before the file uploader


    # 4. File Uploader and Automatic Extraction Logic
    uploaded_file = st.file_uploader(
        # English Label
        "📂 Upload the Financial Report File (PDF/Image) here to start Automatic Extraction:",
        type=["pdf", "png", "jpg", "jpeg"], # Restrict to PDF and images for multimodal extraction
        accept_multiple_files=False
    )
    
    # 5. Automatic Extraction Logic (Executed directly when a file is present)
    if uploaded_file is not None:
        
        if st.session_state.get('last_uploaded_filename') == uploaded_file.name and st.session_state.get('last_uploaded_size') == uploaded_file.size:
             # Skip processing if the file hasn't changed
             st.info(f"File already processed: {uploaded_file.name}. Please check the log above.")
        else:
            # 5.1 Save new file info to prevent reprocessing
            st.session_state.last_uploaded_filename = uploaded_file.name
            st.session_state.last_uploaded_size = uploaded_file.size
            
            # English Message
            st.success(f"File uploaded: {uploaded_file.name}. **Starting Automatic Extraction...**")
            
            if not GEMINI_API_KEY:
                st.error("🚨 Please paste the Gemini API key into the code before starting extraction.")
                return

            # Use Spinner to indicate the process is running
            with st.spinner('⏳ Analyzing and extracting data, preparing the report... (This may take 30-60 seconds)'):
                
                extracted_data = get_llm_multimodal_output(uploaded_file, client)
                
                if extracted_data:
                    
                    # 5.2 Calculate and add the serial number (#) based on current reports count
                    reports_count_for_new_doc = len(all_reports_data) + 1
                    extracted_data["#"] = reports_count_for_new_doc
                    
                    # 5.3 Save Data (to Firebase or temporarily)
                    is_saved = add_report_to_storage(extracted_data)

                    if is_saved:
                        
                        last_report = extracted_data
                        
                        # 5.4 Display Extracted Data for Quick Verification (Arabic/RTL)
                        rtl_markdown(f"✅ Extracted Data for Report No. {last_report['#']} (Quick Check)", "success")
                        st.markdown("---")
                        
                        # Display fields one by one (Arabic fields/values using the RTL helper)
                        for key, value in last_report.items():
                            display_key = fix_arabic(key)
                            display_value = fix_arabic(value)
                            
                            # Using HTML for robust RTL display of field pairs
                            html_line = f"""
                            <div style="direction: rtl; text-align: right; margin-bottom: 5px; line-height: 1.5; font-size: 16px;">
                                <span style="font-weight: bold; color: #155e75;">{display_key}:</span>
                                <span style="margin-right: 5px;">{display_value}</span>
                            </div>
                            """
                            st.markdown(html_line, unsafe_allow_html=True)

                        st.markdown("---")
                        
                        # 5.5 Rerun the app to update the main log table with the new data
                        st.rerun()


if __name__ == '__main__':
    # Initialize session state keys for file handling
    if 'last_uploaded_filename' not in st.session_state:
        st.session_state.last_uploaded_filename = None
    if 'last_uploaded_size' not in st.session_state:
        st.session_state.last_uploaded_size = None
        
    main()
