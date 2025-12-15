import json
import logging
from sqlalchemy import select
from database import SessionLocal, SubsMetaDB, SubsCRUD

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def process_and_update_records():
    """
    Fetches records, processes their query_res data, and updates them.
    """
    # 1. Create a database session
    db = SessionLocal()
    crud = SubsCRUD(db)

    try:
        # 2. Fetch records to process
        # Example: Fetch all records where status is "COMPLETED" or just fetch all
        # You can modify the filter condition as needed
        stmt = select(SubsMetaDB).where(SubsMetaDB.status == "COMPLETED")
        # If you want to process ALL records regardless of status, use: stmt = select(SubsMetaDB)
        
        records = db.scalars(stmt).all()
        logger.info(f"Found {len(records)} records to process.")

        for record in records:
            try:
                logger.info(f"Processing record ID: {record.id}")

                # 3. Get and Parse query_res
                current_query_res_str = record.query_res
                
                # Handle cases where query_res might be empty or None
                if not current_query_res_str:
                    current_data = {}
                else:
                    try:
                        current_data = json.loads(current_query_res_str)
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse JSON for record {record.id}")
                        continue

                # ====================================================
                # 4. Perform your custom logic here
                # ====================================================
                
                # Example Logic: Add a 'processed_timestamp' field to the JSON
                # Replace this block with your actual data manipulation logic
                # import time
                # current_data["updated_by_script"] = True
                # current_data["processed_at"] = time.time()

                summary_url = current_data.get('Summarization')
                chapter_url = current_data.get('AutoChapters')
                transcripts_url = current_data.get('Transcription')

                # 初始化变量，防止 URL 不存在时后面报错
                summary = {}
                chapters = {}
                transcripts = {}

                if summary_url:
                    summary = get_oss_data(summary_url)
                else:
                    logger.warning(f"No Summarization URL for record {record.id}")

                if chapter_url:
                    chapters = get_oss_data(chapter_url)
                else:
                    logger.warning(f"No AutoChapters URL for record {record.id}")

                if transcripts_url:
                    transcripts = get_oss_data(transcripts_url)
                else:
                    logger.warning(f"No Transcription URL for record {record.id}")

                # Example: If you calculated a new value based on query_res
                new_result_value = f"Processed {len(current_data)} keys"
                
                # ====================================================

                # 5. Update the database
                # You can update 'query_res' itself, or other fields like 'summary', 'status', etc.
                
                # Option A: Update query_res with the modified data
                crud.update_task(
                    db_obj=record,
                    summary=summary['Summarization'] if summary else {}, # The CRUD wrapper handles JSON serialization
                    chapters=chapters['AutoChapters'] if chapters else {},
                    transcripts=transcripts['Transcription'] if transcripts else {}
                    # status="UPDATED"      # You can also change status if needed
                )
                
                logger.info(f"Successfully updated record {record.id}")

            except Exception as e:
                logger.error(f"Error processing record {record.id}: {e}")
                # Continue to the next record even if one fails

    except Exception as e:
        logger.error(f"Critical database error: {e}")
    finally:
        # 6. Close the session
        db.close()

import requests

def get_oss_data(url):
    try:
        # 发起同步 GET 请求
        response = requests.get(url)
        
        # 检查状态码
        response.raise_for_status()
        
        # 解析 JSON
        data = response.json()
        
        return data
    except Exception as e:
        logger.error(f"Failed {e}")


if __name__ == "__main__":
    print("Starting update process...")
    process_and_update_records()
    print("Update process finished.")
