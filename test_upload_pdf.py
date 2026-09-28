import os
import boto3
from dotenv import load_dotenv

# Load the keys from your local .env file
load_dotenv()

def test_s3_pdf_upload():
    bucket_name = os.getenv("AWS_S3_BUCKET_NAME")
    
    if not bucket_name:
        print("❌ Error: AWS_S3_BUCKET_NAME is missing from your .env file!")
        return

    # The file path you provided
    file_path = r"C:\Users\easha\Downloads\Chauwk_Progress in Life!! - Enterprise.pdf"
    
    if not os.path.exists(file_path):
        print(f"❌ Error: The file does not exist at {file_path}")
        return

    print(f"Connecting to AWS S3... Target Bucket: {bucket_name}")
    
    # Initialize the S3 client using the keys from your .env
    s3_client = boto3.client(
        's3',
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "ap-south-1")
    )
    
    # Extract just the filename to use as the S3 object key
    file_name = os.path.basename(file_path)
    s3_key = f"test_uploads/{file_name}"
    
    try:
        # Attempt to upload the file to your S3 Bucket
        print(f"Uploading '{file_name}' to S3...")
        s3_client.upload_file(file_path, bucket_name, s3_key)
        print(f"File: {file_name} uploaded in s3")
        
    except Exception as e:
        print(f"Upload Failed! Error details:\n{e}")

if __name__ == "__main__":
    test_s3_pdf_upload()
